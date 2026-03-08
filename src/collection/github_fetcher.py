"""
GitHub API fetcher for collecting issues and comments from microsoft/vscode
"""
import json
import time
from datetime import datetime
from pathlib import Path
from typing import Optional, Generator
import requests
from tqdm import tqdm

import sys
sys.path.append(str(Path(__file__).parent.parent.parent))
from config import GITHUB_REPO, GITHUB_TOKEN, RAW_DATA_DIR, ISSUES_TO_FETCH


class GitHubFetcher:
    """Fetches issues and comments from GitHub API"""
    
    BASE_URL = "https://api.github.com"
    
    def __init__(self, repo: str = GITHUB_REPO, token: Optional[str] = GITHUB_TOKEN):
        self.repo = repo
        self.session = requests.Session()
        self.session.headers.update({
            "Accept": "application/vnd.github.v3+json",
            "User-Agent": "Layer10-Memory-Graph"
        })
        if token:
            self.session.headers["Authorization"] = f"token {token}"
        
        # Rate limiting
        self.requests_remaining = 5000
        self.reset_time = None
    
    def _check_rate_limit(self):
        """Check and handle rate limiting"""
        if self.requests_remaining < 10:
            if self.reset_time:
                wait_time = (self.reset_time - datetime.now()).total_seconds()
                if wait_time > 0:
                    print(f"Rate limited. Waiting {wait_time:.0f} seconds...")
                    time.sleep(wait_time + 1)
    
    def _update_rate_limit(self, response: requests.Response):
        """Update rate limit info from response headers"""
        self.requests_remaining = int(response.headers.get("X-RateLimit-Remaining", 5000))
        reset_timestamp = response.headers.get("X-RateLimit-Reset")
        if reset_timestamp:
            self.reset_time = datetime.fromtimestamp(int(reset_timestamp))
    
    def _get(self, endpoint: str, params: dict = None) -> dict:
        """Make GET request with rate limiting"""
        self._check_rate_limit()
        url = f"{self.BASE_URL}{endpoint}"
        response = self.session.get(url, params=params)
        self._update_rate_limit(response)
        
        if response.status_code == 403:
            # Rate limited
            self._check_rate_limit()
            response = self.session.get(url, params=params)
            self._update_rate_limit(response)
        
        response.raise_for_status()
        return response.json()
    
    def fetch_issues(
        self, 
        limit: int = ISSUES_TO_FETCH,
        state: str = "all",
        sort: str = "updated",
        direction: str = "desc"
    ) -> Generator[dict, None, None]:
        """
        Fetch issues from repository
        
        Args:
            limit: Maximum number of issues to fetch
            state: 'open', 'closed', or 'all'
            sort: 'created', 'updated', or 'comments'
            direction: 'asc' or 'desc'
        
        Yields:
            Issue dictionaries from GitHub API
        """
        endpoint = f"/repos/{self.repo}/issues"
        params = {
            "state": state,
            "sort": sort,
            "direction": direction,
            "per_page": 100  # Max allowed
        }
        
        fetched = 0
        page = 1
        
        with tqdm(total=limit, desc="Fetching issues") as pbar:
            while fetched < limit:
                params["page"] = page
                issues = self._get(endpoint, params)
                
                if not issues:
                    break
                
                for issue in issues:
                    # Skip pull requests (they appear in issues endpoint)
                    if "pull_request" in issue:
                        continue
                    
                    yield issue
                    fetched += 1
                    pbar.update(1)
                    
                    if fetched >= limit:
                        break
                
                page += 1
    
    def fetch_issue_comments(self, issue_number: int) -> list[dict]:
        """Fetch all comments for an issue"""
        endpoint = f"/repos/{self.repo}/issues/{issue_number}/comments"
        params = {"per_page": 100}
        
        all_comments = []
        page = 1
        
        while True:
            params["page"] = page
            comments = self._get(endpoint, params)
            
            if not comments:
                break
            
            all_comments.extend(comments)
            
            if len(comments) < 100:
                break
            
            page += 1
        
        return all_comments
    
    def fetch_issue_events(self, issue_number: int) -> list[dict]:
        """Fetch events (labels, assignments, state changes) for an issue"""
        endpoint = f"/repos/{self.repo}/issues/{issue_number}/events"
        params = {"per_page": 100}
        
        all_events = []
        page = 1
        
        while True:
            params["page"] = page
            events = self._get(endpoint, params)
            
            if not events:
                break
            
            all_events.extend(events)
            
            if len(events) < 100:
                break
            
            page += 1
        
        return all_events
    
    def fetch_issue_with_details(self, issue_number: int) -> dict:
        """Fetch issue with comments and events"""
        endpoint = f"/repos/{self.repo}/issues/{issue_number}"
        issue = self._get(endpoint)
        issue["comments_data"] = self.fetch_issue_comments(issue_number)
        issue["events_data"] = self.fetch_issue_events(issue_number)
        return issue
    
    def collect_and_save(
        self, 
        limit: int = ISSUES_TO_FETCH,
        include_comments: bool = True,
        include_events: bool = True
    ) -> Path:
        """
        Collect issues and save to JSON file
        
        Args:
            limit: Number of issues to fetch
            include_comments: Whether to fetch comments for each issue
            include_events: Whether to fetch events for each issue
        
        Returns:
            Path to saved JSON file
        """
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_file = RAW_DATA_DIR / f"vscode_issues_{timestamp}.json"
        
        all_issues = []
        
        print(f"Collecting {limit} issues from {self.repo}...")
        
        for issue in self.fetch_issues(limit=limit):
            issue_number = issue["number"]
            
            if include_comments and issue.get("comments", 0) > 0:
                try:
                    issue["comments_data"] = self.fetch_issue_comments(issue_number)
                except Exception as e:
                    print(f"Error fetching comments for #{issue_number}: {e}")
                    issue["comments_data"] = []
            else:
                issue["comments_data"] = []
            
            if include_events:
                try:
                    issue["events_data"] = self.fetch_issue_events(issue_number)
                except Exception as e:
                    print(f"Error fetching events for #{issue_number}: {e}")
                    issue["events_data"] = []
            else:
                issue["events_data"] = []
            
            all_issues.append(issue)
        
        # Save to file
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump({
                "repo": self.repo,
                "collected_at": datetime.now().isoformat(),
                "total_issues": len(all_issues),
                "issues": all_issues
            }, f, indent=2, default=str)
        
        print(f"Saved {len(all_issues)} issues to {output_file}")
        return output_file


def main():
    """CLI entry point for data collection"""
    import argparse
    
    parser = argparse.ArgumentParser(description="Fetch GitHub issues")
    parser.add_argument("--limit", type=int, default=ISSUES_TO_FETCH, help="Number of issues")
    parser.add_argument("--no-comments", action="store_true", help="Skip fetching comments")
    parser.add_argument("--no-events", action="store_true", help="Skip fetching events")
    args = parser.parse_args()
    
    fetcher = GitHubFetcher()
    output_file = fetcher.collect_and_save(
        limit=args.limit,
        include_comments=not args.no_comments,
        include_events=not args.no_events
    )
    print(f"Done! Data saved to: {output_file}")


if __name__ == "__main__":
    main()

import os
import re
import requests
import pandas as pd
from dotenv import load_dotenv

load_dotenv()


COGNITIVE_COMPLEXITY_RULE = "java:S3776"

def get_complex_method_issues(
    sonar_url: str = None,
    project_key: str = None,
    token: str = None,
    page_size: int = 500,
) -> dict:
    """
    Fetch methods with excessive cognitive complexity from SonarQube.
    """

    sonar_url = sonar_url or os.getenv("SONAR_URL")
    token = token or os.getenv("SONAR_TOKEN")

    response = requests.get(
        f"{sonar_url}/api/issues/search",
        params={
            "projects": project_key,
            "rules": COGNITIVE_COMPLEXITY_RULE,
            "ps": page_size,
        },
        headers={"Authorization": f"Bearer {token}"},
    )

    response.raise_for_status()

    return response.json()


def extract_complexity_from_message(message: str) -> int:
    """
    Extract current cognitive complexity value from Sonar message.
    Example:
    'Refactor this method to reduce its Cognitive Complexity from 23 to the 15 allowed.'
    """

    match = re.search(r"from (\d+) to the (\d+) allowed", message)

    if not match:
        return 0

    return int(match.group(1))


def parse_issues_to_dataframe(data: dict) -> pd.DataFrame:
    """
    Convert SonarQube issues response to DataFrame.
    """

    rows = []

    for issue in data.get("issues", []):

        component = issue.get("component", "")
        file_path = component.split(":")[-1]

        text_range = issue.get("textRange", {})

        row = {
            "file": file_path,
            "start_line": text_range.get("startLine"),
            "message": issue.get("message"),
            "cognitive_complexity": extract_complexity_from_message(
                issue.get("message", "")
            ),
        }

        rows.append(row)

    return pd.DataFrame(rows)


def fetch_complex_methods(
    sonar_url: str = None,
    project_key: str = None,
    token: str = None,
) -> pd.DataFrame:
    """
    High-level helper for fetching cognitively complex methods.
    """

    data = get_complex_method_issues(
        sonar_url,
        project_key,
        token,
    )

    return parse_issues_to_dataframe(data)
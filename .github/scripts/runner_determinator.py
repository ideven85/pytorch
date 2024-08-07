# flake8: noqa: G004

"""
This runner determinator is used to determine which set of runners to run a
GitHub job on. It uses the first comment of a GitHub issue (by default
https://github.com/pytorch/test-infra/issues/5132) as a user list to determine
which users will get their jobs to run on experimental runners. This user list
is also a comma separated list of additional features or experiments which the
user could be opted in to.

The user list has the following rules:

- Users are GitHub usernames with the @ prefix
- If the first line is a "*" then all users will use the new runners
- If the first line is a "!" then all users will use the old runners
- Each user is also a comma-separated list of features/experiments to enable
- A "#" prefix indicates the user is opted out of the new runners but is opting
  into features/experiments.

Example user list:

    @User1
    @User2,amz2023
    #@UserOptOutOfNewRunner,amz2023
"""

import logging
import os
from argparse import ArgumentParser
from logging import LogRecord
from typing import Any, Iterable

from github import Auth, Github
from github.Issue import Issue


WORKFLOW_LABEL_META = ""  # use meta runners
WORKFLOW_LABEL_LF = "lf."  # use runners from the linux foundation
WORKFLOW_LABEL_LF_CANARY = "lf.c."  # use canary runners from the linux foundation

RUNNER_AMI_LEGACY = ""
RUNNER_AMI_AMZ2023 = "amz2023"

GITHUB_OUTPUT = os.getenv("GITHUB_OUTPUT", "")
GH_OUTPUT_KEY_AMI = "runner-ami"
GH_OUTPUT_KEY_LABEL_TYPE = "label-type"


class ColorFormatter(logging.Formatter):
    """Color codes the log messages based on the log level"""

    COLORS = {
        "WARNING": "\033[33m",  # Yellow
        "ERROR": "\033[31m",  # Red
        "CRITICAL": "\033[31m",  # Red
        "INFO": "\033[0m",  # Reset
        "DEBUG": "\033[0m",  # Reset
    }

    def format(self, record: LogRecord) -> str:
        log_color = self.COLORS.get(record.levelname, "\033[0m")  # Default to reset
        record.msg = f"{log_color}{record.msg}\033[0m"
        return super().format(record)


handler = logging.StreamHandler()
handler.setFormatter(ColorFormatter(fmt="%(levelname)-8s: %(message)s"))

log = logging.getLogger(os.path.basename(__file__))
log.addHandler(handler)
log.setLevel(logging.INFO)


def set_github_output(key: str, value: str) -> None:
    """
    Defines outputs of the github action that invokes this script
    """
    if not GITHUB_OUTPUT:
        # See https://github.blog/changelog/2022-10-11-github-actions-deprecating-save-state-and-set-output-commands/ for deprecation notice
        log.warning(
            "No env var found for GITHUB_OUTPUT, you must be running this code locally. Falling back to the deprecated print method."
        )
        print(f"::set-output name={key}::{value}")
        return

    with open(GITHUB_OUTPUT, "a") as f:
        log.info(f"Setting output: {key}='{value}'")
        f.write(f"{key}={value}\n")


def parse_args() -> Any:
    parser = ArgumentParser("Get dynamic rollout settings")
    parser.add_argument("--github-token", type=str, required=True, help="GitHub token")
    parser.add_argument(
        "--github-issue-repo",
        type=str,
        required=False,
        default="pytorch/test-infra",
        help="GitHub repo to get the issue",
    )
    parser.add_argument(
        "--github-repo",
        type=str,
        required=True,
        help="GitHub repo where CI is running",
    )
    parser.add_argument(
        "--github-issue", type=int, required=True, help="GitHub issue number"
    )
    parser.add_argument(
        "--github-actor", type=str, required=True, help="GitHub triggering_actor"
    )
    parser.add_argument(
        "--github-issue-owner", type=str, required=True, help="GitHub issue owner"
    )
    parser.add_argument(
        "--github-branch", type=str, required=True, help="Current GitHub branch or tag"
    )
    parser.add_argument(
        "--github-ref-type",
        type=str,
        required=True,
        help="Current GitHub ref type, branch or tag",
    )

    return parser.parse_args()


def get_gh_client(github_token: str) -> Github:
    auth = Auth.Token(github_token)
    return Github(auth=auth)


def get_issue(gh: Github, repo: str, issue_num: int) -> Issue:
    repo = gh.get_repo(repo)
    return repo.get_issue(number=issue_num)


def get_potential_pr_author(
    gh: Github, repo: str, username: str, ref_type: str, ref_name: str
) -> str:
    # If the trigger was a new tag added by a bot, this is a ciflow case
    # Fetch the actual username from the original PR. The PR number is
    # embedded in the tag name: ciflow/<name>/<pr-number>
    if username == "pytorch-bot[bot]" and ref_type == "tag":
        split_tag = ref_name.split("/")
        if (
            len(split_tag) == 3
            and split_tag[0] == "ciflow"
            and split_tag[2].isnumeric()
        ):
            pr_number = split_tag[2]
            try:
                repository = gh.get_repo(repo)
                pull = repository.get_pull(number=int(pr_number))
            except Exception as e:
                raise Exception(  # noqa: TRY002
                    f"issue with pull request {pr_number} from repo {repository}"
                ) from e
            return pull.user.login
    # In all other cases, return the original input username
    return username


def is_exception_branch(branch: str) -> bool:
    return branch.split("/")[0] in {"main", "nightly", "release", "landchecks"}


def get_workflow_type(issue: Issue, workflow_requestors: Iterable[str]) -> str:
    try:
        first_comment = issue.get_comments()[0].body.strip("\n\t ")

        if first_comment[0] == "!":
            log.info("LF Workflows are disabled for everyone. Using meta runners.")
            return WORKFLOW_LABEL_META
        elif first_comment[0] == "*":
            log.info("LF Workflows are enabled for everyone. Using LF runners.")
            return WORKFLOW_LABEL_LF
        else:
            all_opted_in_users = {
                usr_raw.strip("\n\t@ ").split(",")[0]
                for usr_raw in first_comment.split()
            }
            opted_in_requestors = {
                usr for usr in workflow_requestors if usr in all_opted_in_users
            }
            if opted_in_requestors:
                log.info(
                    f"LF Workflows are enabled for {', '.join(opted_in_requestors)}. Using LF runners."
                )
                return WORKFLOW_LABEL_LF
            else:
                log.info(
                    f"LF Workflows are disabled for {', '.join(workflow_requestors)}. Using meta runners."
                )
                return WORKFLOW_LABEL_META

    except Exception as e:
        log.error(
            f"Failed to get determine workflow type. Falling back to meta runners. Exception: {e}"
        )
        return WORKFLOW_LABEL_META


def get_optin_feature(
    issue: Issue, workflow_requestors: Iterable[str], feature: str, fallback: str
) -> str:
    try:
        first_comment = issue.get_comments()[0].body.strip("\n\t ")
        userlist = {u.lstrip("#").strip("\n\t@ ") for u in first_comment.split()}
        all_opted_in_users = set()
        for user in userlist:
            for i in user.split(","):
                if i == feature:
                    all_opted_in_users.add(user.split(",")[0])
        opted_in_requestors = {
            usr for usr in workflow_requestors if usr in all_opted_in_users
        }

        if opted_in_requestors:
            log.info(
                f"Feature {feature} is enabled for {', '.join(opted_in_requestors)}. Using feature {feature}."
            )
            return feature
        else:
            log.info(
                f"Feature {feature} is disabled for {', '.join(workflow_requestors)}. Using fallback \"{fallback}\"."
            )
            return fallback

    except Exception as e:
        log.error(
            f'Failed to determine if user has opted-in to feature {feature}. Using fallback "{fallback}". Exception: {e}'
        )
        return fallback


def main() -> None:
    args = parse_args()

    if args.github_ref_type == "branch" and is_exception_branch(args.github_branch):
        log.info(f"Exception branch: '{args.github_branch}', using meta runners")
        label_type = WORKFLOW_LABEL_META
        runner_ami = RUNNER_AMI_LEGACY
    else:
        try:
            gh = get_gh_client(args.github_token)
            # The default issue we use - https://github.com/pytorch/test-infra/issues/5132
            issue = get_issue(gh, args.github_issue_repo, args.github_issue)
            username = get_potential_pr_author(
                gh,
                args.github_repo,
                args.github_actor,
                args.github_ref_type,
                args.github_branch,
            )
            label_type = get_workflow_type(
                issue,
                (
                    args.github_issue_owner,
                    username,
                ),
            )
            runner_ami = get_optin_feature(
                issue=issue,
                workflow_requestors=(
                    args.github_issue_owner,
                    username,
                ),
                feature=RUNNER_AMI_AMZ2023,
                fallback=RUNNER_AMI_LEGACY,
            )
        except Exception as e:
            log.error(
                f"Failed to get issue. Falling back to meta runners. Exception: {e}"
            )
            label_type = WORKFLOW_LABEL_META
            runner_ami = RUNNER_AMI_LEGACY

    # For Canary builds use canary runners
    if args.github_repo == "pytorch/pytorch-canary" and label_type == WORKFLOW_LABEL_LF:
        label_type = WORKFLOW_LABEL_LF_CANARY

    set_github_output(GH_OUTPUT_KEY_LABEL_TYPE, label_type)
    set_github_output(GH_OUTPUT_KEY_AMI, runner_ami)


if __name__ == "__main__":
    main()

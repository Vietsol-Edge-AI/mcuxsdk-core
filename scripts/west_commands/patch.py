# Copyright (c) 2024 Tenstorrent AI ULC
# Copyright 2025 NXP
#
# SPDX-License-Identifier: Apache-2.0

import argparse
import hashlib
import os
import re
import shlex
import subprocess
import sys
import textwrap
import urllib.request
from pathlib import Path

import pykwalify.core
import yaml
from west.commands import WestCommand

script_dir = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
sys.path.append(os.path.join(script_dir, 'misc'))
import mcux_module

MCUX_BASE = Path(__file__).parent.parent.parent

try:
    from yaml import CSafeDumper as SafeDumper
    from yaml import CSafeLoader as SafeLoader
except ImportError:
    from yaml import SafeDumper, SafeLoader

WEST_PATCH_SCHEMA_PATH = Path(__file__).parents[1] / "schemas" / "patch-schema.yml"
with open(WEST_PATCH_SCHEMA_PATH) as f:
    patches_schema = yaml.load(f, Loader=SafeLoader)

WEST_PATCH_BASE = Path("mcuxsdk") / "patches"
WEST_PATCH_YAML = Path("mcuxsdk") / "patches.yml"


class Patch(WestCommand):
    def __init__(self):
        super().__init__(
            "patch",
            "apply patches to the west workspace",
            "Apply patches to the west workspace",
            accepts_unknown_args=False,
        )

    def do_add_parser(self, parser_adder):
        parser = parser_adder.add_parser(
            self.name,
            help=self.help,
            formatter_class=argparse.RawDescriptionHelpFormatter,
            description=self.description,
            epilog=textwrap.dedent("""\
            Applying Patches:

                Run "west patch apply" to apply patches.
                See "west patch apply --help" for details.

            Cleaning Patches:

                Run "west patch clean" to clean patches.
                See "west patch clean --help" for details.

            Listing Patches:

                Run "west patch list" to list patches.
                See "west patch list --help" for details.

            Fetching Patches:

                Run "west patch gh-fetch" to fetch patches from Github.
                See "west patch gh-fetch --help" for details.

            YAML File Format:

            The patches.yml syntax is described in "scripts/schemas/patch-schema.yml".

            patches:
              - path: zephyr/kernel-pipe-fix-not-k-no-wait-and-ge-min-xfer-bytes.patch
                sha256sum: e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855
                module: zephyr
                author: Kermit D. Frog
                email: itsnoteasy@being.gr
                date: 2020-04-20
                upstreamable: true
                merge-pr: https://github.com/zephyrproject-rtos/zephyr/pull/24486
                issue: https://github.com/zephyrproject-rtos/zephyr/issues/24485
                merge-status: true
                merge-commit: af926ae728c78affa89cbc1de811ab4211ed0f69
                merge-date: 2020-04-27
                apply-command: git apply
                comments: |
                  Songs about rainbows - why are there so many??
                custom:
                  possible-muppets-to-ask-for-clarification-with-the-above-question:
                    - Miss Piggy
                    - Gonzo
                    - Fozzie Bear
                    - Animal
            """),
        )

        parser.add_argument(
            "-b",
            "--patch-base",
            help=f"""
                Directory containing patch files (absolute or relative to module dir,
                default: {WEST_PATCH_BASE})""",
            metavar="DIR",
            type=Path,
        )
        parser.add_argument(
            "-l",
            "--patch-yml",
            help=f"""
                Path to patches.yml file (absolute or relative to module dir,
                default: {WEST_PATCH_YAML})""",
            metavar="FILE",
            type=Path,
        )
        parser.add_argument(
            "-w",
            "--west-workspace",
            help="West workspace",
            metavar="DIR",
            type=Path,
        )
        parser.add_argument(
            "-sm",
            "--src-module",
            dest="src_module",
            metavar="MODULE",
            type=str,
            help="""
                Zephyr module containing the patch definition (name, absolute path or
                path relative to west-workspace)""",
        )
        parser.add_argument(
            "-dm",
            "--dst-module",
            action="append",
            dest="dst_modules",
            metavar="MODULE",
            type=str,
            help="""
                Zephyr module to run the 'patch' command for.
                Option can be passed multiple times.
                If this option is not given, the 'patch' command will run for Zephyr
                and all modules.""",
        )

        subparsers = parser.add_subparsers(
            dest="subcommand",
            metavar="<subcommand>",
            help="select a subcommand. If omitted treat it as 'list'",
        )

        apply_arg_parser = subparsers.add_parser(
            "apply",
            help="Apply patches",
            formatter_class=argparse.RawDescriptionHelpFormatter,
            epilog=textwrap.dedent(
                """
            Applying Patches:

                Run "west patch apply" to apply patches.
            """
            ),
        )
        apply_arg_parser.add_argument(
            "-r",
            "--roll-back",
            help="Roll back if any patch fails to apply",
            action="store_true",
            default=False,
        )

        subparsers.add_parser(
            "clean",
            help="Clean patches",
            formatter_class=argparse.RawDescriptionHelpFormatter,
            epilog=textwrap.dedent(
                """
            Cleaning Patches:

                Run "west patch clean" to clean patches.
            """
            ),
        )

        gh_fetch_arg_parser = subparsers.add_parser(
            "gh-fetch",
            help="Fetch patch from Github",
            formatter_class=argparse.RawDescriptionHelpFormatter,
            epilog=textwrap.dedent(
                """
            Fetching Patches from Github:

                Run "west patch gh-fetch" to fetch a PR from Github and store it as a patch.
                The meta data is generated and appended to the provided patches.yml file.

                If no patches.yml file exists, it will be created.

            Example:
            west patch gh-fetch --owner nxp-mcuxpresso --repo mcuxsdk-core --pull-request 18 --module core
            """
            ),
        )
        gh_fetch_arg_parser.add_argument(
            "-o",
            "--owner",
            action="store",
            default="zephyrproject-rtos",
            help="Github repository owner",
        )
        gh_fetch_arg_parser.add_argument(
            "-r",
            "--repo",
            action="store",
            default="zephyr",
            help="Github repository",
        )
        gh_fetch_arg_parser.add_argument(
            "-pr",
            "--pull-request",
            metavar="ID",
            action="store",
            required=True,
            type=int,
            help="Github Pull Request ID",
        )
        gh_fetch_arg_parser.add_argument(
            "-m",
            "--module",
            metavar="DIR",
            action="store",
            required=True,
            type=Path,
            help="Module path",
        )
        gh_fetch_arg_parser.add_argument(
            "-s",
            "--split-commits",
            action="store_true",
            help="Create patch files for each commit instead of a single patch for the entire PR",
        )
        gh_fetch_arg_parser.add_argument(
            '-t',
            '--token',
            metavar='FILE',
            dest='tokenfile',
            help='File containing GitHub token (alternatively, use GITHUB_TOKEN env variable)',
        )

        bb_fetch_arg_parser = subparsers.add_parser(
            "bb-fetch",
            help="Fetch patch from Bitbucket",
            formatter_class=argparse.RawDescriptionHelpFormatter,
            epilog=textwrap.dedent("""
            Fetching Patches from Bitbucket:
            Run "west patch bb-fetch" to fetch a PR from Bitbucket and store it as a patch.

            Example:
            west patch bb-fetch --owner MCUCORE --repo mcuxsdk-core --pull-request 1123 --module core
            """),
        )

        bb_fetch_arg_parser.add_argument(
            "-s",
            "--split-commits",
            action="store_true",
            help="Create patch files for each commit instead of a single patch for the entire PR",
        )

        bb_fetch_arg_parser.add_argument(
            "-o", 
            "--owner", 
            help="Bitbucket project key (e.g., MCUCORE)", 
            required=True
        )

        bb_fetch_arg_parser.add_argument(
            "-r", 
            "--repo", 
            help="Bitbucket repository slug (e.g., mcuxsdk-core)", 
            required=True
        )

        bb_fetch_arg_parser.add_argument(
            "-pr", 
            "--pull-request", 
            metavar="ID", 
            type=int, 
            required=True,
            help="Bitbucket Pull Request ID"
        )

        bb_fetch_arg_parser.add_argument(
            "-m", 
            "--module", 
            metavar="DIR", 
            type=Path, 
            required=True
        )

        bb_fetch_arg_parser.add_argument(
            "-t", 
            "--tokenfile", 
            metavar="FILE", 
            help="File with Bitbucket token"
        )
        
        bb_fetch_arg_parser.add_argument(
            "-u",
            "--username",
            help="Bitbucket username (alternatively, use BITBUCKET_USERNAME env variable)"
        )

        bb_fetch_arg_parser.add_argument(
            "-p",
            "--password",
            help="Bitbucket password or Personal Access Token (alternatively, use BITBUCKET_PASSWORD env variable)"
        )

        bb_fetch_arg_parser.add_argument(
            "--base-url",
            action="store",
            default="https://bitbucket.sw.nxp.com",
            help="Bitbucket server base URL (default: https://bitbucket.sw.nxp.com)"
        )

        subparsers.add_parser(
            "list",
            help="List patches",
            formatter_class=argparse.RawDescriptionHelpFormatter,
            epilog=textwrap.dedent(
                """
            Listing Patches:

                Run "west patch list" to list patches.
            """
            ),
        )

        return parser

    def filter_args(self, args):
        try:
            manifest_path = self.config.get("manifest.path")
        except BaseException:
            self.die("could not retrieve manifest path from west configuration")

        topdir = Path(self.topdir)

        if args.src_module is not None:
            mod_path = self.get_module_path(args.src_module)
            if mod_path is None:
                self.die(f'Source module "{args.src_module}" not found')
            if args.patch_base is not None and args.patch_base.is_absolute():
                self.die("patch-base must not be an absolute path in combination with src-module")
            if args.patch_yml is not None and args.patch_yml.is_absolute():
                self.die("patch-yml must not be an absolute path in combination with src-module")
            manifest_dir = topdir / mod_path
        else:
            manifest_dir = topdir / manifest_path

        if args.patch_base is None:
            args.patch_base = manifest_dir / WEST_PATCH_BASE
        if not args.patch_base.is_absolute():
            args.patch_base = manifest_dir / args.patch_base

        if args.patch_yml is None:
            args.patch_yml = manifest_dir / WEST_PATCH_YAML
        elif not args.patch_yml.is_absolute():
            args.patch_yml = manifest_dir / args.patch_yml

        if args.west_workspace is None:
            args.west_workspace = topdir
        elif not args.west_workspace.is_absolute():
            args.west_workspace = topdir / args.west_workspace

        if args.dst_modules is not None:
            args.dst_modules = [self.get_module_path(m) for m in args.dst_modules]

    def load_yml(self, args, allow_missing):
        if not os.path.isfile(args.patch_yml):
            if not allow_missing:
                self.inf(f"no patches to apply: {args.patch_yml} not found")
                return None

            # Return the schema defaults
            return pykwalify.core.Core(source_data={}, schema_data=patches_schema).validate()

        try:
            with open(args.patch_yml) as f:
                yml = yaml.load(f, Loader=SafeLoader)
            return pykwalify.core.Core(source_data=yml, schema_data=patches_schema).validate()
        except (yaml.YAMLError, pykwalify.errors.SchemaError) as e:
            self.die(f"ERROR: Malformed yaml {args.patch_yml}: {e}")

    def do_run(self, args, _):
        self.filter_args(args)

        west_config = Path(args.west_workspace) / ".west" / "config"
        if not os.path.isfile(west_config):
            self.die(f"{args.west_workspace} is not a valid west workspace")

        yml = self.load_yml(args, args.subcommand in ["gh-fetch", "bb-fetch"])
        if yml is None:
            return

        if not args.subcommand:
            args.subcommand = "list"

        method = {
            "apply": self.apply,
            "clean": self.clean,
            "list": self.list,
            "gh-fetch": self.gh_fetch,
            "bb-fetch": self.bb_fetch,
        }

        method[args.subcommand](args, yml, args.dst_modules)

    def apply(self, args, yml, dst_mods=None):
        patches = yml.get("patches", [])
        if not patches:
            return

        patch_count = 0
        failed_patch = None
        patched_mods = set()
        for patch_info in patches:
            mod = self.get_module_path(patch_info["module"])
            if mod is None:
                continue

            if dst_mods and mod not in dst_mods:
                continue

            pth = patch_info["path"]
            patch_path = os.path.realpath(Path(args.patch_base) / pth)

            apply_cmd = patch_info["apply-command"]
            apply_cmd_list = shlex.split(apply_cmd)

            self.dbg(f"reading patch file {pth}")
            expect_sha256 = patch_info["sha256sum"]
            try:
                actual_sha256 = self.get_file_sha256sum(patch_path)
            except Exception as e:
                self.err(f"failed to read {pth}: {e}")
                failed_patch = pth
                break

            if actual_sha256 != expect_sha256:
                self.dbg("FAIL")
                self.err(
                    f"sha256 mismatch for {pth}:\n"
                    f"expect: {expect_sha256}\n"
                    f"actual: {actual_sha256}"
                )
                failed_patch = pth
                break
            self.dbg("OK")
            patch_count += 1

            mod_path = Path(args.west_workspace) / mod
            patched_mods.add(mod)

            self.dbg(f"patching {mod}... ", end="")
            apply_cmd += patch_path
            apply_cmd_list.extend([patch_path])
            proc = subprocess.run(
                apply_cmd_list, capture_output=True, cwd=mod_path, encoding="utf-8"
            )
            if proc.returncode:
                self.dbg("FAIL")
                self.err(proc.stderr)
                failed_patch = pth
                break
            self.dbg("OK")

        if not failed_patch:
            self.inf(f"{patch_count} patches applied successfully \\o/")
            return

        if args.roll_back:
            self.clean(args, yml, patched_mods)

        self.die(f"failed to apply patch {failed_patch}")

    def clean(self, args, yml, dst_mods=None):
        clean_cmd = yml["clean-command"]
        checkout_cmd = yml["checkout-command"]

        if not clean_cmd and not checkout_cmd:
            self.dbg("no clean or checkout commands specified")
            return

        clean_cmd_list = shlex.split(clean_cmd)
        checkout_cmd_list = shlex.split(checkout_cmd)

        for mod in yml.get("patches", []):
            m = self.get_module_path(mod.get("module"))
            if m is None:
                continue
            if dst_mods and m not in dst_mods:
                continue
            mod_path = Path(args.west_workspace) / m

            try:
                if checkout_cmd:
                    self.dbg(f"Running '{checkout_cmd}' in {mod}.. ", end="")
                    proc = subprocess.run(
                        checkout_cmd_list, capture_output=True, cwd=mod_path, encoding="utf-8"
                    )
                    if proc.returncode:
                        self.dbg("FAIL")
                        self.err(f"{checkout_cmd} failed for {mod}\n{proc.stderr}")
                    else:
                        self.dbg("OK")

                if clean_cmd:
                    self.dbg(f"Running '{clean_cmd}' in {mod}.. ", end="")
                    proc = subprocess.run(
                        clean_cmd_list, capture_output=True, cwd=mod_path, encoding="utf-8"
                    )
                    if proc.returncode:
                        self.dbg("FAIL")
                        self.err(f"{clean_cmd} failed for {mod}\n{proc.stderr}")
                    else:
                        self.dbg("OK")

            except Exception as e:
                # If this fails for some reason, just log it and continue
                self.err(f"failed to clean up {mod}: {e}")

    def list(self, args, yml, dst_mods=None):
        patches = yml.get("patches", [])
        if not patches:
            return

        for patch_info in patches:
            if dst_mods and self.get_module_path(patch_info["module"]) not in dst_mods:
                continue
            self.inf(patch_info)

    def gh_fetch(self, args, yml, mods=None):
        if mods:
            self.die(
                "Module filters are not available for the gh-fetch subcommand, "
                "pass a single -m/--module argument after the subcommand."
            )
        try:
            from github import Auth, Github
        except ImportError:
            self.die("PyGithub not found; can be installed with 'pip install PyGithub'")

        gh = Github(auth=Auth.Token(args.tokenfile) if args.tokenfile else None)
        pr = gh.get_repo(f"{args.owner}/{args.repo}").get_pull(args.pull_request)
        args.patch_base.mkdir(parents=True, exist_ok=True)

        if args.split_commits:
            for cm in pr.get_commits():
                subject = cm.commit.message.splitlines()[0]
                filename = "-".join(filter(None, re.split("[^a-zA-Z0-9]+", subject))) + ".patch"

                # No patch URL is provided by the API, but appending .patch to the HTML works too
                urllib.request.urlretrieve(f"{cm.html_url}.patch", args.patch_base / filename)

                patch_info = {
                    "path": filename,
                    "sha256sum": self.get_file_sha256sum(args.patch_base / filename),
                    "module": str(args.module),
                    "author": cm.commit.author.name or "Hidden",
                    "email": cm.commit.author.email or "hidden@github.com",
                    "date": cm.commit.author.date.strftime("%Y-%m-%d"),
                    "upstreamable": True,
                    "merge-pr": pr.html_url,
                    "merge-status": pr.merged,
                }

                yml.setdefault("patches", []).append(patch_info)
                self.inf(f"  ✓ Created {args.patch_base}/{filename}")
        else:
            filename = "-".join(filter(None, re.split("[^a-zA-Z0-9]+", pr.title))) + ".patch"
            urllib.request.urlretrieve(pr.patch_url, args.patch_base / filename)

            patch_info = {
                "path": filename,
                "sha256sum": self.get_file_sha256sum(args.patch_base / filename),
                "module": str(args.module),
                "author": pr.user.name or "Hidden",
                "email": pr.user.email or "hidden@github.com",
                "date": pr.created_at.strftime("%Y-%m-%d"),
                "upstreamable": True,
                "merge-pr": pr.html_url,
                "merge-status": pr.merged,
            }

            yml.setdefault("patches", []).append(patch_info)
            self.inf(f"  ✓ Created {args.patch_base}/{filename}")

        args.patch_yml.parent.mkdir(parents=True, exist_ok=True)
        with open(args.patch_yml, "w") as f:
            yaml.dump(yml, f, Dumper=SafeDumper)

    def _convert_bitbucket_commit_to_patch(self, diff_data, commit_data, pr_data):
        """Convert a single commit's Bitbucket diff into standard Git patch format."""
        import datetime

        lines = []

        # get commit information
        commit_id = commit_data.get('id', '')
        author_name = commit_data.get('author', {}).get('displayName', 'Unknown')
        author_email = commit_data.get('author', {}).get('emailAddress', 'unknown@nxp.com')
        commit_msg = commit_data.get('message', '').strip()
        timestamp = commit_data.get('authorTimestamp', 0)

        # Git patch head
        date_obj = datetime.datetime.fromtimestamp(timestamp / 1000)

        # Standard git format-patch
        lines.append(f"From {commit_id} Mon Sep 17 00:00:00 2001")
        lines.append(f"From: {author_name} <{author_email}>")
        lines.append(f"Date: {date_obj.strftime('%a, %d %b %Y %H:%M:%S %z')}")

        # Commit message
        commit_lines = commit_msg.splitlines()
        if commit_lines:
            lines.append(f"Subject: [PATCH] {commit_lines[0]}")
            lines.append("")

            # Add commit message body
            if len(commit_lines) > 1:
                for line in commit_lines[1:]:
                    lines.append(line)
                lines.append("")

        lines.append("---")

        # Summary diffs
        files_changed = len(diff_data.get('diffs', []))
        if files_changed > 0:
            lines.append(f" {files_changed} file{'s' if files_changed != 1 else ''} changed")
            lines.append("")

        # Handle diff for each file
        for diff in diff_data.get('diffs', []):
            source = diff.get('source', {})
            destination = diff.get('destination', {})

            # Get file path
            src_path = source.get('toString', '/dev/null') if source else '/dev/null'
            dst_path = destination.get('toString', '/dev/null') if destination else '/dev/null'

            # Skip truncated diffs
            if diff.get('truncated', False):
                lines.append(f"# Diff for {dst_path} was truncated")
                lines.append("")
                continue

            # diff --git line
            if src_path != '/dev/null' and dst_path != '/dev/null':
                lines.append(f"diff --git a/{src_path} b/{dst_path}")
            elif src_path == '/dev/null':
                lines.append(f"diff --git a/{dst_path} b/{dst_path}")
            else:
                lines.append(f"diff --git a/{src_path} b/{src_path}")

            # Hnadle file mode change
            if not source and destination:
                # new file
                lines.append("new file mode 100644")
                lines.append("index 0000000..0000000")
                lines.append(f"--- /dev/null")
                lines.append(f"+++ b/{dst_path}")
            elif source and not destination:
                # delete file
                lines.append("deleted file mode 100644")
                lines.append("index 0000000..0000000")
                lines.append(f"--- a/{src_path}")
                lines.append(f"+++ /dev/null")
            else:
                # Modify file
                lines.append("index 0000000..0000000 100644")
                lines.append(f"--- a/{src_path}")
                lines.append(f"+++ b/{dst_path}")

            # Hnadle each hunk
            hunks = diff.get('hunks', [])
            if not hunks:
                # If there are no hunks, it might be a binary file or an empty change.
                lines.append("")
                continue

            for hunk in hunks:
                src_line = hunk.get('sourceLine', 0)
                src_span = hunk.get('sourceSpan', 0)
                dst_line = hunk.get('destinationLine', 0)
                dst_span = hunk.get('destinationSpan', 0)

                # @@ line
                lines.append(f"@@ -{src_line},{src_span} +{dst_line},{dst_span} @@")

                # Handle each segment
                for segment in hunk.get('segments', []):
                    seg_type = segment.get('type', 'CONTEXT')

                    for line_obj in segment.get('lines', []):
                        # Note：Bitbucket API return line field may not contain newline characters.
                        line_text = line_obj.get('line', '')

                        # Add prefix based on segment type
                        if seg_type == 'REMOVED':
                            lines.append(f"-{line_text}")
                        elif seg_type == 'ADDED':
                            lines.append(f"+{line_text}")
                        else:  # CONTEXT
                            lines.append(f" {line_text}")

            # empty line between files
            lines.append("")

        # Git patch ending mark
        lines.append("-- ")
        lines.append("2.34.1")
        lines.append("")

        return '\n'.join(lines)

    def bb_fetch(self, args, yml, mods=None):
        if mods:
            self.die(
                "Module filters are not available for the bb-fetch subcommand, "
                "pass a single -m/--module argument after the subcommand."
            )

        import requests
        import getpass
        from requests.auth import HTTPBasicAuth
        import datetime

        # Obtain authentication information.
        username = args.username or\
            os.getenv('USERNAME') or \
            input('Input NXP wbi id (e.g, nxa16738):')

        password = args.password or\
            os.getenv('PASSWORD') or \
            getpass.getpass('Input NXP wbi password (Use Powershell or provide by --password, it will stuck in Gitbash!!!):')

        if not username or not password:
            self.die("Bitbucket credentials required. Use --username/--password or set USERNAME/PASSWORD env variables")

        # Use Basic Authentication
        auth = HTTPBasicAuth(username, password)
        base_url = args.base_url.rstrip('/')
        api_base = f"{base_url}/rest/api/latest"
        pr_url = f"{api_base}/projects/{args.owner}/repos/{args.repo}/pull-requests/{args.pull_request}"

        try:
            # Get PR information
            response = requests.get(pr_url, auth=auth, verify=True, timeout=30)
            response.raise_for_status()
            pr_data = response.json()

            self.inf(f"PR Title: {pr_data.get('title')}")
            self.inf(f"PR State: {pr_data.get('state')}")

            args.patch_base.mkdir(parents=True, exist_ok=True)

            if args.split_commits:
                # Get all commits from PR
                commits_url = f"{pr_url}/commits"
                commits_response = requests.get(
                    commits_url,
                    auth=auth,
                    verify=True,
                    params={'limit': 1000},
                    timeout=30
                )
                commits_response.raise_for_status()
                commits_data = commits_response.json()

                commits = commits_data.get('values', [])
                self.inf(f"Found {len(commits)} commits in PR #{args.pull_request}")

                if not commits:
                    self.die(f"No commits found in PR #{args.pull_request}")

                commits.reverse()
                # Create patch for each commit
                for idx, commit in enumerate(commits, 1):
                    commit_id = commit.get('id')
                    commit_msg = commit.get('message', '').strip()
                    commit_subject = commit_msg.splitlines()[0] if commit_msg else f'commit-{idx}'

                    self.inf(f"Processing commit {idx}/{len(commits)}: {commit_id[:8]} - {commit_subject}")

                    # Create patch name
                    safe_subject = re.sub(r'[^\w\s-]', '', commit_subject[:50])
                    safe_subject = re.sub(r'[-\s]+', '-', safe_subject).strip('-')
                    filename = f"{idx:04d}-{safe_subject}.patch" if safe_subject else f"{idx:04d}-commit.patch"

                    # Get commit diff
                    commit_diff_url = f"{api_base}/projects/{args.owner}/repos/{args.repo}/commits/{commit_id}/diff"
                    commit_diff_response = requests.get(
                        commit_diff_url,
                        auth=auth,
                        verify=True,
                        params={
                            'contextLines': 3,
                            'whitespace': 'show'  # Keep empty character
                        },
                        timeout=30
                    )
                    commit_diff_response.raise_for_status()
                    commit_diff_data = commit_diff_response.json()

                    # Translate to patch format
                    patch_content = self._convert_bitbucket_commit_to_patch(commit_diff_data, commit, pr_data)
                    patch_file = args.patch_base / filename

                    # Use Unix EOF
                    with open(patch_file, 'w', encoding='utf-8', newline='\n') as f:
                        f.write(patch_content)

                    # Get commit author information
                    author_name = commit.get('author', {}).get('displayName', 'Unknown')
                    author_email = commit.get('author', {}).get('emailAddress', 'unknown@nxp.com')
                    commit_date = datetime.datetime.fromtimestamp(
                        commit.get('authorTimestamp', 0) / 1000
                    ).strftime("%Y-%m-%d")

                    patch_info = {
                        "path": filename,
                        "sha256sum": self.get_file_sha256sum(patch_file),
                        "module": str(args.module),
                        "author": author_name,
                        "email": author_email,
                        "date": commit_date,
                        "upstreamable": True,
                        "merge-pr": f"{base_url}/projects/{args.owner}/repos/{args.repo}/pull-requests/{args.pull_request}",
                        "merge-status": pr_data.get('state') == 'MERGED',
                        "apply-command": "git apply"
                    }

                    yml.setdefault("patches", []).append(patch_info)
                    self.inf(f"  ✓ Created {patch_file}")

                self.inf(f"\nSuccessfully created {len(commits)} patch files")
            else:
                # Create single patch
                pr_title = pr_data.get('title', f'PR-{args.pull_request}')
                safe_title = re.sub(r'[^\w\s-]', '', pr_title)
                safe_title = re.sub(r'[-\s]+', '-', safe_title).strip('-')
                filename = f"{safe_title}.patch" if safe_title else f"PR-{args.pull_request}.patch"
                # Get PR diff
                diff_url = f"{pr_url}/diff"
                diff_response = requests.get(
                    diff_url, 
                    auth=auth, 
                    verify=True,
                    params={
                        'contextLines': 3,
                        'whitespace': 'show'
                    },
                    timeout=30
                )
                diff_response.raise_for_status()
                diff_data = diff_response.json()
                # Transform patch format
                patch_content = self._convert_bitbucket_diff_to_patch(diff_data, pr_data)
                patch_file = args.patch_base / filename

                # Use Unix EOF
                with open(patch_file, 'w', encoding='utf-8', newline='\n') as f:
                    f.write(patch_content)

                # Get author info
                author_name = pr_data.get('author', {}).get('user', {}).get('displayName', 'Unknown')
                author_email = pr_data.get('author', {}).get('user', {}).get('emailAddress', 'unknown@nxp.com')
                created_date = datetime.datetime.fromtimestamp(
                    pr_data.get('createdDate', 0) / 1000
                ).strftime("%Y-%m-%d")

                patch_info = {
                    "path": filename,
                    "sha256sum": self.get_file_sha256sum(patch_file),
                    "module": str(args.module),
                    "author": author_name,
                    "email": author_email,
                    "date": created_date,
                    "upstreamable": True,
                    "merge-pr": f"{base_url}/projects/{args.owner}/repos/{args.repo}/pull-requests/{args.pull_request}",
                    "merge-status": pr_data.get('state') == 'MERGED',
                    "apply-command": "git apply"
                }

                yml.setdefault("patches", []).append(patch_info)
                self.inf(f"✓ Created {patch_file}")

            # Save updated patches.yml
            args.patch_yml.parent.mkdir(parents=True, exist_ok=True)
            with open(args.patch_yml, "w", encoding='utf-8') as f:
                yaml.dump(yml, f, Dumper=SafeDumper, default_flow_style=False, allow_unicode=True)

            self.inf(f"Successfully fetched patch from Bitbucket PR #{args.pull_request}")

        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 401:
                self.die("Authentication failed. Please check your username and password/token")
            elif e.response.status_code == 404:
                self.die(f"PR #{args.pull_request} not found in {args.owner}/{args.repo}")
            else:
                self.die(f"HTTP error: {e.response.status_code} - {e.response.text}")
        except requests.exceptions.RequestException as e:
            self.die(f"Request failed: {e}")
        except Exception as e:
            import traceback
            traceback.print_exc()
            self.die(f"Error processing Bitbucket PR: {e}")

    def _convert_bitbucket_diff_to_patch(self, diff_data, pr_data):
        """Convert Bitbucket PR diff JSON to unified diff patch 格式"""
        import datetime

        lines = []

        # Add patch header
        author = pr_data.get('author', {}).get('user', {}).get('displayName', 'Unknown')
        email = pr_data.get('author', {}).get('user', {}).get('emailAddress', 'unknown@nxp.com')
        title = pr_data.get('title', 'No title')
        description = pr_data.get('description', '')

        date = datetime.datetime.fromtimestamp(pr_data.get('createdDate', 0) / 1000).isoformat()

        lines.append(f"From: {author} <{email}>")
        lines.append(f"Date: {date}")
        lines.append(f"Subject: {title}")
        lines.append("")

        if description:
            lines.append(description)
            lines.append("")

        # Handle diff for each file
        for diff in diff_data.get('diffs', []):
            source = diff.get('source', {})
            destination = diff.get('destination', {})

            src_path = source.get('toString', '/dev/null')
            dst_path = destination.get('toString', '/dev/null')

            lines.append(f"--- a/{src_path}")
            lines.append(f"+++ b/{dst_path}")

            # Hnadle each hunk
            for hunk in diff.get('hunks', []):
                src_line = hunk.get('sourceLine', 0)
                src_span = hunk.get('sourceSpan', 0)
                dst_line = hunk.get('destinationLine', 0)
                dst_span = hunk.get('destinationSpan', 0)

                lines.append(f"@@ -{src_line},{src_span} +{dst_line},{dst_span} @@")

                # Handle each segment
                for segment in hunk.get('segments', []):
                    seg_type = segment.get('type', 'CONTEXT')

                    for line in segment.get('lines', []):
                        line_text = line.get('line', '')

                        if seg_type == 'REMOVED':
                            lines.append(f"-{line_text}")
                        elif seg_type == 'ADDED':
                            lines.append(f"+{line_text}")
                        else:  # CONTEXT
                            lines.append(f" {line_text}")

            lines.append("")

        return '\n'.join(lines)

    @staticmethod
    def get_file_sha256sum(filename: Path) -> str:
        # Read as text to normalize line endings
        with open(filename, encoding="utf-8", newline=None) as fp:
            content = fp.read()

        # NOTE: If python 3.11 is the minimum, the following can be replaced with:
        # digest = hashlib.file_digest(BytesIO(content_bytes), "sha256")
        digest = hashlib.new("sha256")
        digest.update(content.encode("utf-8"))

        return digest.hexdigest()

    def get_module_path(self, module_name_or_path):
        
        if module_name_or_path is None:
            return None

        topdir = Path(self.topdir)

        if Path(module_name_or_path).is_absolute():
            if Path(module_name_or_path).is_dir():
                return Path(module_name_or_path).resolve().relative_to(topdir)
            return None

        if (topdir / module_name_or_path).is_dir():
            return Path(module_name_or_path)

        all_modules = mcux_module.parse_modules(MCUX_BASE, self.manifest)

        for m in all_modules:
            if m.meta['name'] == module_name_or_path:
                return Path(m.project).relative_to(topdir)

        path = self.get_west_project_path(module_name_or_path)

        if path:
            return path

        return None

    def get_west_project_path(self, module_name):
        """
        Get the project path for a given module name from west projects
        
        Args:
            module_name: The name of the module to find
            
        Returns:
            Path object of the module's location, or None if not found
        """
        if module_name is None:
            return None

        # Special handling for 'core' module
        if module_name == "core":
            return MCUX_BASE

        west_projs = mcux_module.west_projects(self.manifest)

        if west_projs is None:
            self.dbg(f"No west projects found, cannot locate module '{module_name}'")
            return None

        projects = west_projs.get('projects', [])

        # Search through all projects
        for project in projects:
            # Check if project name matches
            if hasattr(project, 'name') and project.name == module_name:
                return Path(project.posixpath)

            # Check if project path basename matches
            if hasattr(project, 'posixpath'):
                project_path = Path(project.posixpath)
                if project_path.name == module_name:
                    return project_path

        self.dbg(f"Module '{module_name}' not found in west projects or mcux modules")

        return None

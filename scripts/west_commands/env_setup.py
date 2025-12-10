#!/usr/bin/python3

# Copyright 2024 NXP
#
# SPDX-License-Identifier: BSD-3-Clause

import os
import platform
import hashlib
import shutil
from pathlib import Path
from west.commands import WestCommand
from west import configuration as config

RB_VERION_NT = '3.1.2'
RB_VERSION_POSIX = '3.1.4'

SCRIPT_DIR = Path(__file__).parent.parent
PORTABLE_RUBY_BASE = 'https://github.com/Homebrew/homebrew-portable-ruby/releases/tag/3.1.4'

def get_file_md5(filename):
    digest = hashlib.md5()
    with open(filename, "rb") as f:
        while chunk := f.read(2**18):
            digest.update(chunk)
    return digest.hexdigest()

class PlatformNotSupported(Exception):
    def __init__(self, arch, sys_name, msg=None, *args, **kwargs):
        msg = msg or f"""portable_ruby does not support {arch}_{sys_name}. You can only use it under Windows, x86_64-linux and arm64-darwin.
For other Unix-like systems, we strongly suggest you use https://github.com/rbenv/rbenv.
For more details, please refer GUIDE, or contact us."""
        super().__init__(msg, *args, **kwargs)

class InstallRuby(WestCommand):
    def __init__(self):
        super().__init__(
            'install_ruby',
            'Install portable ruby with gems for guiproject generation.',
            ''
        )
        if os.name == 'nt':
            self.install_path = Path.home()
            self.version = RB_VERION_NT
        else:
            self.install_path = Path.home()
            self.version = RB_VERSION_POSIX

    def do_add_parser(self, parser_adder):
        parser = parser_adder.add_parser(self.name,
                                         help=self.help,
                                         description=self.description)
        parser.add_argument('-p', '--path', action='store', default=None, help='The path to hold portable ruby. the default location is user home path.')

        return parser

    # TODO Align windows archive with linux/macos format
    def install_ruby_for_windows(self):
        cwd = os.getcwd()
        ruby_path = self.install_path / 'portable-ruby'
        install_script_path = SCRIPT_DIR / 'resources/portable_ruby_nt/setup.bat'
        self.run_subprocess([install_script_path.as_posix(), str(ruby_path)], shell=True)

        os.chdir(cwd)
        return (install_script_path / 'bin').as_posix()

    def install_ruby_for_linux(self, sys_arch):
        return self._for_posix(sys_arch)

    def install_ruby_for_darwin(self, sys_arch):
        return self._for_posix(sys_arch)

    def do_run(self, args, unknown_args):
        if args.path:
            input_path = Path(args.path).resolve()
            if not input_path.exists():
                self.wrn(f'{args.path} does not exist, will create it.')
                input_path.mkdir(parents=True, exist_ok=True)
            self.install_path = input_path

        arch = platform.machine()
        sys_name = platform.system()
        try:
            if arch in ['x86_64', 'AMD64']:
                if sys_name == 'Windows':
                    self.ruby_bin = self.install_ruby_for_windows()
                elif sys_name == 'Linux':
                    self.ruby_bin = self.install_ruby_for_linux('x86_64-linux')
                else:
                    raise PlatformNotSupported(arch, sys_name)
            elif arch in ['arm64']:
                if sys_name == 'Darwin':
                    self.ruby_bin = self.install_ruby_for_darwin('arm64-darwin')
                else:
                    raise PlatformNotSupported(arch, sys_name)
            else:
                raise PlatformNotSupported(arch, sys_name)
        except PlatformNotSupported as exec:
            print(str(exec))
            self.ruby_bin = None
        except Exception as exec:
            print(str(exec))
            self.ruby_bin = None
        finally:
            if not self.ruby_bin:
                self.die("Failed to install portable_ruby")
            config.update_config('env', 'ruby', self.ruby_bin)

    def _for_posix(self, sys_arch):
        if self._extract_archive(sys_arch, SCRIPT_DIR / 'resources' / f'portable_ruby_{sys_arch}.tar.xz'):
            ruby_bin = (self.install_path / 'portable-ruby' / self.version / 'bin').as_posix()
            sys_rb = shutil.which("ruby")
            if sys_rb and ruby_bin not in sys_rb:
                self.inf(f"""The active ruby is {sys_rb}
Please append following line in your shell profile like .zshrc or .bashrc:
  export PATH={ruby_bin}:$PATH{os.linesep}""")
            return ruby_bin
        return None

    def _extract_archive(self, sys_arch: str, archive_path: Path):
        ruby_path = self.install_path / 'portable-ruby'
        if not archive_path.exists():
            self.err(f'{archive_path} does not exist!')
            return False
        file_md5 = get_file_md5(archive_path)
        if (checksum_file := ruby_path / file_md5).exists():
            self.inf('You have already install the latest portable ruby')
            return True
        if ruby_path.exists():
            shutil.rmtree(ruby_path)
        self.banner(f"Start extract portable-ruby for {sys_arch}, it was created based on {PORTABLE_RUBY_BASE}")
        # The archive already have 'portable-ruby' wrapper
        shutil.unpack_archive(archive_path, extract_dir=self.install_path)
        open(checksum_file, 'w').close()
        return True

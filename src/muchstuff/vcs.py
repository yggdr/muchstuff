import abc
from collections.abc import Callable, Generator, Iterable, Mapping
from os import PathLike
from pathlib import Path
import re
import subprocess
try:
    import tomllib
except ImportError:
    import tomli as tomllib
import textwrap
from typing import Any, ClassVar
try:
    from typing import Self
except ImportError:
    from typing_extensions import Self

import unidiff


class VCSError(Exception):
    pass


class VCSOperationError(VCSError):
    pass


class VCS(metaclass=abc.ABCMeta):
    VCS: ClassVar[dict[str, type[Self]]] = {}
    name: str
    dest: Path
    source: Path

    def __init__(self, attrs: Mapping[str, Any]):
        if not {'name', 'dest', 'source'} <= attrs.keys():
            raise RuntimeError('name, source, and dest are required')
        for name, value in attrs.items():
            setattr(self, name, value)

    @classmethod
    def get_vcs(cls, vcsname: str, repo_info: dict[str, Any]) -> Self:
        return cls.VCS[vcsname](repo_info)

    def __init_subclass__(cls, /, vcsname: str, altnames: Iterable[str] | None = None, **kw):
        super().__init_subclass__(**kw)
        cls.VCS[vcsname] = cls
        if altnames is not None:
            for altname in altnames:
                cls.VCS[altname] = cls

    def exec(self, *proc_args: PathLike | str) -> subprocess.CompletedProcess:
        try:
            return subprocess.run(proc_args, capture_output=True, text=True, check=True)
        except subprocess.CalledProcessError as cpe:
            raise VCSOperationError(
                textwrap.dedent(f'''
Error while working in repo {self.name}:
{cpe.cmd} returned code {cpe.returncode}:
Output: "{cpe.stdout}"
Err: "{cpe.stderr}"''')
            ) from cpe

    @abc.abstractmethod
    def clone(self) -> str:
        pass

    @abc.abstractmethod
    def update(self) -> str:
        pass

    @abc.abstractmethod
    def diff(self, *args: str | PathLike) -> str:
        pass

    @abc.abstractmethod
    def commits(self, *args: str, with_diff: bool = False) -> str:
        pass

    @classmethod
    def get_diff_args_from_update_msg(cls, txt: str) -> tuple[str] | None:
        return cls.get_diff_args_from_update_lines(txt.split('\n'))

    @staticmethod
    @abc.abstractmethod
    def get_diff_args_from_update_lines(lines: Iterable[str]) -> tuple[str] | None:
        pass

    @staticmethod
    @abc.abstractmethod
    def split_into_commits(lines: Iterable[str]) -> list[str]:
        pass

    def update_or_clone(self) -> Callable[[], str]:
        return self.update if self.dest.exists() else self.clone


class Git(VCS, vcsname='git'):
    def clone(self) -> str:
        # Git clone always outputs to stderr when being piped
        return self.exec('git', 'clone', self.source, self.dest).stderr

    def update(self) -> str:
        p = self.exec('git', '-C', self.dest, 'pull')
        return p.stdout if p.returncode == 0 else p.stderr

    def diff(self, *args: str | PathLike) -> str:
        return self.exec('git', '-C', self.dest, 'diff', *args).stdout.rstrip()

    def commits(self, *args: str, with_diff: bool = False) -> str:
        log_with_args = ['log']
        if with_diff:
            log_with_args.append('-p')
        return self.exec('git', '-C', self.dest, *log_with_args, *args).stdout

    @staticmethod
    def _check_for_new_commit_start(line):
        return re.match(r'^commit [a-fA-F0-9]+$', line) is not None

    def split_into_commits(self, lines: Iterable[str]) -> Generator[str]:
        commit = []
        for line in lines:
            if self._check_for_new_commit_start(line) and commit:
                yield '\n'.join(commit)
                commit = [line]
            else:
                commit.append(line)
        yield '\n'.join(commit)

    def split_into_files(self, lines: Iterable[str]) -> Generator[str]:
        for pfile in unidiff.PatchSet(line+'\n' for line in lines):
            if pfile.is_added_file:
                title = f"+ {pfile.path}"
            elif pfile.is_removed_file:
                title = f"- {pfile.path}"
            elif pfile.is_modified_file:
                title = f"📝 {pfile.path}"
            elif pfile.is_rename:
                title = f"{pfile.source_file} -> {pfile.target_file}"
            else:
                raise RuntimeError('UNREACHABLE')

            yield f"{title}\n{pfile}"

    @staticmethod
    def get_diff_args_from_update_lines(lines: Iterable[str]) -> tuple[str] | None:
        prefix = 'Updating '
        for line in lines:
            if line.startswith(prefix):
                return (line[len(prefix):], )


class Mercurial(VCS, vcsname='mercurial', altnames=['hg']):
    def clone(self) -> str:
        p = self.exec('hg', 'clone', self.source, self.dest)
        return p.stdout if p.returncode == 0 else p.stderr

    def update(self) -> str:
        p = self.exec('hg', '--cwd', self.dest, 'pull', '--update')
        return p.stdout if p.returncode == 0 else p.stderr

    def diff(self, *args: str | PathLike) -> str:
        p = self.exec('hg', '--cwd', self.dest, 'diff', *args)
        return p.stdout if p.returncode == 0 else p.stderr

    def commits(self, *args: str, with_diff: bool = False) -> str:
        log_with_args = ['log']
        if with_diff:
            log_with_args.append('-p')
        match args:
            case ['--from', from_, '--to', to]:
                new_args = f"{from_}:{to}"
            case ['--from', from_]:
                new_args = f"{from_[:-1]}:"
            case _:
                raise RuntimeError("UNREACHABLE")
        p = self.exec('hg', '--cwd', self.dest, *log_with_args, *new_args)
        return p.stdout if p.returncode == 0 else p.stderr

    def split_into_commits():
        pass

    def get_diff_args_from_update_lines(lines: Iterable[str]) -> tuple[str] | None:
        prefix = 'new changesets '
        for line in lines:
            if line.startswith(prefix):
                match line[len(prefix):].split(':'):
                    case [from_]:
                        return '--from', f'{from_}^'
                    case [from_, to]:
                        return '--from', from_, '--to', to
                    case _:
                        raise RuntimeError(f'Mercurial "new changesets" line should not have more than one ":": {line}')


def get_repos(configpath: Path | str | None = None) -> Generator[VCS, None, None]:
    with open(Path(configpath if configpath is not None else '~/.config/muchstuff.toml').expanduser(), 'rb') as conffile:
        conf = tomllib.load(conffile)
    _DEFAULTS = conf.pop('_DEFAULTS', {})
    for name, repo_info in conf.items():
        if isinstance(repo_info, dict):
            repo_info = _DEFAULTS | repo_info
            repo_info['name'] = name
            repo_info['dest'] = Path(repo_info['dest']).expanduser()
            repo_info['source'] = Path(repo_info['source']).expanduser()
            yield VCS.get_vcs(repo_info['type'], repo_info)

import abc
from collections.abc import Callable, Generator, Iterable, Mapping
from os import PathLike
from pathlib import Path
import subprocess
import tomllib
from typing import Any, ClassVar, Self


class VCS(metaclass=abc.ABCMeta):
    VCS: ClassVar[dict[str, type[Self]]] = {}
    name: str
    dest: Path
    source: Path

    def __init__(self, attrs: Mapping[str, Any]):
        if not {'name', 'dest', 'source'} < attrs.keys():
            raise RuntimeError('name, source, and dest are required')
        self.last_update: str = ''
        for name, value in attrs.items():
            setattr(self, name, value)

    @classmethod
    def get_vcs(cls, vcsname: str, repo_info: dict[str, Any]) -> Self:
        return cls.VCS[vcsname](repo_info)

    def exec(self, *proc_args: PathLike | str) -> subprocess.CompletedProcess:
        print('RUNNING')
        print('!'*100)
        print(proc_args)
        self._proc_args = proc_args
        return subprocess.run(proc_args, capture_output=True, text=True)

    def __init_subclass__(cls, /, name: str, altnames: Iterable[str] | None = None, **kw):
        super().__init_subclass__(**kw)
        cls.VCS[name] = cls
        if altnames is not None:
            for altname in altnames:
                cls.VCS[altname] = cls

    def update_or_clone(self) -> Callable[[], str]:
        return self.update if self.dest.exists() else self.clone

    @abc.abstractmethod
    def clone(self) -> str:
        pass

    @abc.abstractmethod
    def update(self) -> str:
        pass

    @abc.abstractmethod
    def diff(self, *args: str | PathLike) -> str:
        pass

    @classmethod
    def get_diff_args_from_update_msg(cls, txt: str) -> tuple[str] | None:
        return cls.get_diff_args_from_update_lines(txt.split('\n'))

    @staticmethod
    @abc.abstractmethod
    def get_diff_args_from_update_lines(lines: Iterable[str]) -> tuple[str] | None:
        pass


class Git(VCS, name='git'):
    def clone(self) -> str:
        # Git clone always outputs to stderr when being piped
        return self.exec('git', 'clone', self.source, self.dest).stderr

    def update(self) -> str:
        p = self.exec('git', '-C', self.dest, 'pull')
        return p.stdout if p.returncode == 0 else p.stderr

    def diff(self, *args: str | PathLike) -> str:
        return self.exec('git', '-C', self.dest, 'diff', *args).stdout

    @staticmethod
    def get_diff_args_from_update_lines(lines: Iterable[str]) -> tuple[str] | None:
        prefix = 'Updating '
        for line in lines:
            if line.startswith(prefix):
                return (line[len(prefix):], )


class Mercurial(VCS, name='mercurial', altnames=['hg']):
    def clone(self) -> str:
        p = self.exec('hg', 'clone', self.source, self.dest)
        return p.stdout if p.returncode == 0 else p.stderr

    def update(self) -> str:
        p = self.exec('hg', '--cwd', self.dest, 'pull', '--update')
        return p.stdout if p.returncode == 0 else p.stderr

    def diff(self, *args: str | PathLike) -> str:
        p = self.exec('hg', '--cwd', self.dest, 'diff', *args)
        return p.stdout if p.returncode == 0 else p.stderr

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
    with open(Path(configpath if configpath is not None else '~/.config/repos.toml').expanduser(), 'rb') as conffile:
        conf = tomllib.load(conffile)
    _DEFAULTS = conf.pop('_DEFAULTS', {})
    for name, repo_info in conf.items():
        if isinstance(repo_info, dict):
            repo_info = _DEFAULTS | repo_info
            repo_info['name'] = name
            repo_info['dest'] = Path(repo_info['dest']).expanduser()
            repo_info['source'] = Path(repo_info['source']).expanduser()
            yield VCS.get_vcs(repo_info['type'], repo_info)

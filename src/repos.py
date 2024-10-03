import abc
from os import PathLike
from pathlib import Path
import subprocess
import tomllib
import typing


class VCS(metaclass=abc.ABCMeta):
    VCS: dict[str, typing.Type[typing.Self]] = {}
    name: str
    dest: Path
    source: Path

    def __init__(self, attrs: typing.Mapping[str, typing.Any]):
        if not {'name', 'dest', 'source'} < attrs.keys():
            raise RuntimeError('name, source, and dest are required')
        self.last_update: str = ''
        for name, value in attrs.items():
            setattr(self, name, value)

    @classmethod
    def get_vcs(cls, vcsname: str, repo_info: dict[str, typing.Any]) -> typing.Self:
        return cls.VCS[vcsname](repo_info)

    def exec(self, *proc_args: PathLike | str) -> subprocess.CompletedProcess:
        return subprocess.run(proc_args, capture_output=True, text=True)

    def __init_subclass__(cls, /, name: str, altnames: typing.Iterable[str] | None = None, **kw):
        super().__init_subclass__(**kw)
        cls.VCS[name] = cls
        if altnames is not None:
            for altname in altnames:
                cls.VCS[altname] = cls

    def update_or_clone(self) -> typing.Callable[[], str]:
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

    @abc.abstractmethod
    def get_diff_args_from_update_msg(self, txt: str) -> str | None:
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

    @classmethod
    def get_diff_args_from_update_msg(cls, txt: str) -> str | None:
        return cls.get_diff_args_from_update_lines(txt.split('\n'))

    @staticmethod
    def get_diff_args_from_update_lines(lines: typing.Iterable[str]) -> str | None:
        prefix = 'Updating '
        for line in lines:
            if line.startswith(prefix):
                return line[len(prefix):]


# class Mercurial(VCS, name='mercurial', altnames=['hg']):
#     pass


def get_repos(configpath: Path | str = '~/.config/repos.toml') -> typing.Generator[VCS, None, None]:
    with open(Path(configpath).expanduser(), 'rb') as conffile:
        conf = tomllib.load(conffile)
    _DEFAULTS = conf.pop('_DEFAULTS', {})
    for name, repo_info in conf.items():
        if isinstance(repo_info, dict):
            repo_info = _DEFAULTS | repo_info
            repo_info['name'] = name
            repo_info['dest'] = Path(repo_info['dest']).expanduser()
            repo_info['source'] = Path(repo_info['source']).expanduser()
            yield VCS.get_vcs(repo_info['type'], repo_info)

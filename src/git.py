from collections.abc import Iterable
from os import PathLike
import urllib
import warnings

from .repos import CLIEngine, Commit, Diff, LibEngine, Repo, VCS


try:
    import pygit2
except ImportError:
    pygit2 = None
    warnings.warn("Cannot find 'pygit2' python library. Git "
                  "repositories will only awailable via the 'git' command, "
                  "limiting available features",
                  ImportWarning)


class GitRepo(Repo):
    pass

class GitCommit(Commit):
    pass

class GitDiff(Diff):
    pass


class Git(VCS, name='git', libname='pygit2'):

    class LibEngine(LibEngine):
        def clone(self) -> GitRepo:
            self.repo = pygit2.clone_repository(self._fix_file_schema(self.vcs.source), self.vcs.dest)

        def update(self) -> GitRepo:
            if self.repo is None:
                self.repo = pygit2.Repository(self.vcs.dest)
            self.repo.head

        @staticmethod
        def _fix_file_schema(url: str):
            parsed = urllib.parse.urlparse(url)
            if parsed.schema == '':
                return urllib.parse.urlunparse(parsed._replace(schema='file'))
            return url

    class CLIEngine(CLIEngine):
        def clone(self) -> str:
            # Git clone always outputs to stderr when being piped
            return self.exec('git', 'clone', self.source, self.dest).stderr

        def update(self) -> str:
            p = self.exec('git', '-C', self.dest, 'pull')
            return p.stdout if p.returncode == 0 else p.stderr

        def diff(self, *args: str | PathLike) -> str:
            return self.exec('git', '-C', self.dest, 'diff', *args).stdout

        def commits(self, *args: str) -> str:
            return self.exec('git', '-C', self.dest, 'log', *args).stdout

        @staticmethod
        def get_diff_args_from_update_lines(lines: Iterable[str]) -> tuple[str] | None:
            prefix = 'Updating '
            for line in lines:
                if line.startswith(prefix):
                    return (line[len(prefix):], )

import nox

_pyvers = ['3.10', '3.11', '3.12', '3.13']

def prep(session):
    toml = nox.project.load_toml('pyproject.toml')
    session.install(*toml['project']['dependencies'])
    session.install('.')

@nox.session(python=_pyvers)
def run(session):
    prep(session)
    session.run('muchstuff')

@nox.session(python=_pyvers)
def version(session):
    prep(session)
    session.run('muchstuff', '--version')

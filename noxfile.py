import nox

_pyvers = ['3.10', '3.11', '3.12', '3.13']

def prep(session, dev=False):
    toml = nox.project.load_toml('pyproject.toml')
    session.install(*toml['project']['dependencies'])
    if dev:
        session.install(*toml['dependency-groups']['dev'])
    session.install('.')

@nox.session(python=_pyvers)
def run(session):
    prep(session)
    session.run('muchstuff')

@nox.session(python=_pyvers)
def debugrun(session):
    prep(session, dev=True)
    session.run('muchstuff', '-d')

@nox.session(python=_pyvers)
def version(session):
    prep(session)
    session.run('muchstuff', '--version')

# muchstuff

Small utility tool to keep interesting code repositories up to date while
also seeing what changed in them.

## Why?

Whenever I notice that I have a local clone of some software project lying
around, I wonder what new stuff has been introduced since the last time I
looked at it. To answer that question I usually go into the repo, cleanup
whatever local modifications I might have made, switch to their master/main
branch, and just pull. Then I can diff the old and new HEAD and see all the
exciting new stuff.

To automate the latter part of pulling the new stuff and viewing the diff, I
decided to write a little helper tool. There are of course quite a few tools
out there that keep a list of repositories synchronised, but getting them to
let me view the changes how I wanted to didn't seem easily possible. Coupled
with my desire to test out the [textual](https://textual.textualize.io) TUI
framework lead to me writing this.

## How?

Install `muchstuff` with the tool of your choice, e.g. `pipx(u) install
muchstuff`, give it a `~/.config/muchstuff.toml` looking like so:

```toml
[_DEFAULTS]
type = 'git'

[somerepo]
dest = '~/mywork/somerepo'
source = 'git@gitlab.mycompany.biz:mywork/somerepo'

[muchstuff]
dest = '/nas-storage/corerepos/muchstuff'
source = 'git@github.com:yggdr/muchstuff'
```

Now muchstuff will, upon starting, pull from all these repositories, and show
up to 4 outputs per repo:

- The result of the pull, as in the output of e.g. `git pull`
- The diff for each file changed
- The commits it just pulled
- The commits it just pulled, with the changes per commit.

## Known bugs

There are still a few issues that I haven't had the time or motivation to work
out. Use at your own risk.

Mercurial was supported in an older, less powerfull version. I'm not sure when
I will get around getting that up to speed again.

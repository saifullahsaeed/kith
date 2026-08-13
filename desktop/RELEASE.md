# Building a Kith you can hand to someone

`PACKAGING.md` is the roadmap for making Kith shippable. This is the part that
works today: producing `release/Kith-<version>-arm64.dmg` and what the person
receiving it has to do.

## Letting CI do it

`.github/workflows/release.yml` does all of the below on every push to the default
branch — but only when the version in `desktop/package.json` has no tag yet. So
cutting a release is one edit:

```sh
npm --prefix desktop version patch   # or edit the version by hand
git push
```

Push without touching the version and the workflow stops in seconds, having found
`v<version>` already tagged. That check is deliberately "does the tag exist"
rather than "did package.json change in this push", because the diff answer is
wrong on a re-run, on a squash, and on the first run.

Two things it cannot do, both because notarization needs a paid Apple Developer
account: the download is still ad-hoc signed, and the release notes therefore
carry the System Settings instructions. And note the runner is `macos-15` because
the frozen server is a native arm64 binary — this is not cross-compilable, and the
artifact is arm64 because the runner is.

## Building it

Three artifacts have to exist, in this order, because each one is baked into the
next:

```sh
cd ui      && npm run build      # the interface
cd ../server && .venv/bin/pyinstaller kith-server.spec --noconfirm
cd ../desktop && npm run build   # the shell's main process (tsc)
cd desktop && npx electron-builder --mac dmg --arm64
```

`npm run dist` chains the first two for you. The reason to know the long form is
that a failure in the middle is otherwise silent about which half broke.

The order is not stylistic. The built interface is copied *inside* the frozen
server (`kith-server.spec` refuses to build without `ui/dist`), and the frozen
server is copied inside the app bundle by `extraResources`. Build them out of
order and you ship yesterday's interface with today's server, which looks like a
bug in the app rather than a bug in the build.

Result: `release/Kith-0.1.0-arm64.dmg`, about 140 MB. The `.app` inside carries
its own Python, its own server and its own copy of the interface — nothing is
fetched at first launch.

**arm64 only.** For an Intel Mac the frozen server must be built again by an x64
Python; the arm64 bundle will not run under Rosetta because the Mach-O inside it
is arm64. Universal means freezing twice and merging.

## Signing, and what your friend will see

There is no *Developer ID Application* certificate for this project. An Apple
Development certificate is a different thing — it signs builds for machines in
your own provisioning profile, and Gatekeeper rejects it anywhere else.

So the app is **ad-hoc signed** by `scripts/adhoc-sign.js` (an `afterPack` hook),
and `mac.identity` is `null` so electron-builder does not attempt a real signing
it cannot do. This is not cosmetic: on Apple Silicon every mach-O needs *some*
valid signature to execute at all, and packaging rewrites the bundle, which
invalidates the one Electron shipped with. An unsigned arm64 app does not warn —
it fails to launch.

An ad-hoc signature is not notarized. `spctl -a` reports `rejected`, and the
first launch on someone else's Mac is blocked with "Apple could not verify Kith is
free of malware." That is expected, and it is a one-time step.

**Not** right-click → Open. That bypass worked for years and stopped working in
macOS 15; on anything current it produces the same refusal as double-clicking.
Anyone who remembers the old trick will conclude the app is broken. The route now
is System Settings, and the order is load-bearing:

> 1. Double-click Kith and let it be blocked. Click **Done**.
> 2. **System Settings → Privacy & Security → Security** (below FileVault and
>    Firewall). There is now a line reading *"Kith" was blocked to protect your
>    Mac* → **Open Anyway**.
> 3. Authenticate, then **Open Anyway** once more in the confirmation.

Step 1 is not optional: the button does not exist until something has been
blocked, and it expires about an hour afterwards. Someone who goes looking in
System Settings *first* will find nothing there and reasonably give up.

After that it launches normally forever. The exception is per-app, not per-launch.

The quarantine flag can also be cleared directly, which skips all of the above:

```sh
xattr -dr com.apple.quarantine /Applications/Kith.app
```

Making this warning go away is not a build setting — it needs an Apple Developer
account, a Developer ID certificate, `hardenedRuntime: true` and a `notarize`
step. Nothing else changes.

## What the person still has to supply

The app installs nothing. It also cannot think without a model, and this is the
one thing it cannot bring with it — a model is either a service you have an
account with or a multi-gigabyte download.

Kith's default is `qwen3:4b` on a local Ollama at `127.0.0.1:11434`, so **out of
the box on a fresh Mac it starts, opens, and has no brain.** Two ways to fix that,
in Settings:

- **An OpenRouter key** — paste it in Settings, pick a model. Nothing to install.
  This is the path to recommend.
- **Ollama** — `brew install ollama`, then `ollama pull qwen3:4b`, and
  `ollama pull nomic-embed-text` for semantic recall. An install, but nothing
  leaves the machine.

Semantic memory degrades to keyword search without the embedding model, so
skipping `nomic-embed-text` costs recall quality rather than function.

Everything else is genuinely optional. Docker is not required — it is only a
fallback route to a SearXNG instance, and there is no sandbox any more. Language
servers (`diagnostics`, `references`, `definition`, `rename_symbol`) are
discovered if present and simply left out of the prompt if not.

## Where his data goes

`~/.kith` — `agent.db`, `config.db`, `api.token`, `skills/`, and `server.log`.
The shell passes it as `KITH_DATA_DIR` and the frozen server defaults to the same
path, so the two agree without configuration. `server.log` is appended, not
truncated, because the log of the run that failed is the one you want.

Uninstalling is dragging the app to the bin; deleting `~/.kith` is what actually
forgets him.

## Verifying a build before you send it

The failure mode worth ruling out is an app that launches on the machine that
built it and dies everywhere else, so test a *copy*, outside the build tree:

```sh
hdiutil attach release/Kith-0.1.0-arm64.dmg
cp -R "/Volumes/Kith 0.1.0-arm64/Kith.app" /tmp/
codesign --verify --deep --strict /tmp/Kith.app        # must pass
/tmp/Kith.app/Contents/MacOS/Kith                       # must open a window
```

Then check the window is not empty and `~/.kith/server.log` has a fresh
`launching` line — that is the shell having found and started its own server
rather than attaching to one you already had running. If you have a dev server on
:8611 it will attach to that instead and tell you nothing; stop it first
(`./run stop`), or point the test somewhere else:

```sh
KITH_ORIGIN=http://127.0.0.1:8699 PORT=8699 KITH_DATA_DIR=/tmp/kithtest \
  /tmp/Kith.app/Contents/MacOS/Kith
```

Note `ELECTRON_RUN_AS_NODE` in your environment will make the app start as plain
Node and reject its own arguments with `bad option`. That is the shell, not the
build.

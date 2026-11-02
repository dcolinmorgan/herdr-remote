# Iris integration checks

These tests use three throwaway Iris identities, a private in-memory Nostr relay,
real Herdr 0.9.5, herdr-remote and Pi 1.4.0. A deterministic model fixture asks Pi
to read a marker file; no model account, paid inference or personal profile is used.
The run covers pairing, owner-only commands, output, completion/blocked notices,
receipts, interrupts and restart replay protection. It does not test mobile push,
Bluetooth, or remote approval controls.

## Run in Docker

From the repository root, with Python 3 and Docker available:

```sh
python3 tests/iris/prepare.py
docker build --platform linux/amd64 -f tests/iris/Dockerfile -t herdr-iris-test .
docker run --rm --platform linux/amd64 --network none --cap-drop ALL \
  --security-opt no-new-privileges --cpus 2 --memory 2g --pids-limit 256 \
  herdr-iris-test
docker run --rm --platform linux/amd64 --network none --cap-drop ALL \
  --security-opt no-new-privileges --cpus 2 --memory 2g --pids-limit 256 \
  -e UV_OFFLINE=1 herdr-iris-test sh tests/run.sh
```

Both containers are removed on exit. Neither mounts a host directory or Docker
socket, publishes ports, or can access the internet, host services or a VPN.
Test keys and histories live only in the container. Downloads and the Docker
build need internet; every fetched executable and Python dependency is checked
against the committed hashes before use. `inputs.lock.json` pins Linux amd64
release artifacts and the Python container digest; `requirements.lock` pins the
test dependencies. The image uses Python 3.13.18 on Debian trixie. These are
verified release binaries, not a claim of rebuilding their source reproducibly.

The ordinary test suite discovers `tests/test_iris.py`. For focused checks:

```sh
uv run --with websockets==15.0.1 python -m unittest discover -s tests -p test_iris.py -v
```

The local relay and model fixture come from the MIT-licensed
[`irislib/iris-chat-channels`](https://github.com/irislib/iris-chat-channels/tree/ccc5020d3622652ba9a606e8f15484dcc877be63/tests)
at `ccc5020d3622652ba9a606e8f15484dcc877be63`; `LICENSE` is included here.
The relay deliberately omits signature verification and is only a test fixture.
The actual Iris clients perform encryption, decryption and peer authentication.

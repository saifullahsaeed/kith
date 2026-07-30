# Kith's own machine — a small Debian box it manages through tools.
# Isolated from the host: it runs unprivileged, with resource limits, no host
# mounts, and no access to the Docker socket.
FROM debian:bookworm-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
      python3 python3-pip python3-venv \
      curl wget ca-certificates git jq ripgrep unzip sqlite3 \
      less nano vim-tiny tree coreutils procps \
 && rm -rf /var/lib/apt/lists/*

# Common libraries pre-installed so real research/coding works out of the box
# (Debian's pip is externally-managed, hence --break-system-packages).
RUN pip3 install --no-cache-dir --break-system-packages \
      requests httpx beautifulsoup4 lxml python-dateutil \
      rich pyyaml tabulate pandas numpy playwright

# A headless browser so he can read JS-rendered sites (SPAs) that plain curl
# can't. Browsers live at a fixed image path (not his home volume) so they're
# always found regardless of $HOME at runtime.
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
RUN apt-get update \
 && python3 -m playwright install --with-deps chromium \
 && rm -rf /var/lib/apt/lists/*

# The render helper his browse_page tool calls.
COPY browse.py /opt/kith/browse.py

# A non-root user; /home/kith is its home (persisted in a named volume).
RUN useradd --create-home --shell /bin/bash kith
WORKDIR /home/kith
USER kith

# A little note so home isn't empty on first boot.
RUN printf '%s\n' \
      "This machine is yours, Kith." \
      "Home is /home/kith. Organize it however you like." \
      "Python already has requests, httpx, bs4, lxml, pandas, numpy, rich." \
      > /home/kith/README.txt

CMD ["sleep", "infinity"]

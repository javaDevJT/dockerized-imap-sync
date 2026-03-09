FROM gilleslamiral/imapsync:latest

USER root

RUN apt-get update \
    && apt-get install -y --no-install-recommends cron \
    && rm -rf /var/lib/apt/lists/*

ENV IMAP1_HOST="imap.mail.yahoo.com" \
    IMAP1_USER="" \
    IMAP1_PASSWORD="" \
    IMAP2_HOST="imap.gmail.com" \
    IMAP2_USER="" \
    IMAP2_PASSWORD=""

RUN printf '%s\n' \
    '#!/bin/sh' \
    'set -eu' \
    '' \
    ': "${IMAP1_HOST:?IMAP1_HOST is required}"' \
    ': "${IMAP1_USER:?IMAP1_USER is required}"' \
    ': "${IMAP1_PASSWORD:?IMAP1_PASSWORD is required}"' \
    ': "${IMAP2_HOST:?IMAP2_HOST is required}"' \
    ': "${IMAP2_USER:?IMAP2_USER is required}"' \
    ': "${IMAP2_PASSWORD:?IMAP2_PASSWORD is required}"' \
    '' \
    'exec imapsync \' \
    '  --host1 "$IMAP1_HOST" \' \
    '  --user1 "$IMAP1_USER" \' \
    '  --password1 "$IMAP1_PASSWORD" --ssl1 \' \
    '  --host2 "$IMAP2_HOST" \' \
    '  --user2 "$IMAP2_USER" \' \
    '  --password2 "$IMAP2_PASSWORD" --ssl2 \' \
    '  --delete1 --expunge1 \' \
    '  --automap \' \
    '  --nofoldersizesatend \' \
    '  --nofoldersizes \' \
    '  --compress1 \' \
    '  --nocompress2' \
    > /usr/local/bin/run-imapsync \
    && chmod +x /usr/local/bin/run-imapsync

RUN printf '%s\n' \
    'SHELL=/bin/sh' \
    'PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin' \
    '*/3 * * * * root /usr/local/bin/run-imapsync >> /var/log/imapsync.log 2>&1' \
    > /etc/cron.d/imapsync \
    && chmod 0644 /etc/cron.d/imapsync \
    && touch /var/log/imapsync.log

CMD ["cron", "-f"]

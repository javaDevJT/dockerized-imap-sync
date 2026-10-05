# syntax=docker/dockerfile:1

FROM alpine:3.24 AS imapsync-source

ADD --checksum=sha256:34b7ed8e0948b3f9ccac0318333b726a7263e134784eee6eab429639808b7822 \
    https://github.com/imapsync/imapsync/archive/93654c6025ff7814f983ab74dd300f9bed9282d9.tar.gz \
    /tmp/imapsync.tar.gz
RUN tar -xzf /tmp/imapsync.tar.gz -C /tmp \
    && install -Dm755 /tmp/imapsync-93654c6025ff7814f983ab74dd300f9bed9282d9/imapsync /usr/local/bin/imapsync

FROM alpine:3.24 AS zlib-build

RUN apk add --no-cache alpine-sdk
WORKDIR /src/zlib-ng-compat
COPY packaging/zlib-ng-compat/APKBUILD ./APKBUILD
RUN abuild-keygen -a -n \
    && REPODEST=/packages abuild -F -r \
    && cp /packages/*/*/zlib-ng-compat-*.apk /zlib-ng-compat.apk

FROM alpine:3.24

COPY --from=zlib-build /etc/apk/keys/ /etc/apk/keys/
COPY --from=zlib-build /zlib-ng-compat.apk /tmp/zlib-ng-compat.apk
# zlib-ng supplies the same libz ABI without zlib's vulnerable gz_vacate code.
# Keep the replacement as an APK so the SBOM records its real name and version.
RUN apk add --no-cache /tmp/zlib-ng-compat.apk \
    && apk del zlib \
    && apk upgrade --no-cache \
    && apk add --no-cache \
        ca-certificates \
        perl \
        perl-digest-hmac \
        perl-encode-imaputf7 \
        perl-file-copy-recursive \
        perl-file-tail \
        perl-io-socket-inet6 \
        perl-io-socket-ssl \
        perl-io-tee \
        perl-mail-imapclient \
        perl-ntlm \
        perl-readonly \
        perl-regexp-common \
        perl-sys-meminfo \
        perl-term-readkey \
        perl-test-simple \
        perl-unicode-string \
    && rm /tmp/zlib-ng-compat.apk \
    && ! apk info --exists zlib

COPY --from=imapsync-source /usr/local/bin/imapsync /usr/local/bin/imapsync
RUN perl -MCompress::Zlib -e 'die "compression round trip failed" unless Compress::Zlib::uncompress(Compress::Zlib::compress("imapsync")) eq "imapsync"' \
    && imapsync --version \
    && imapsync --help >/dev/null

USER root

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
    '  --nocompress1 \' \
    '  --nocompress2' \
    > /usr/local/bin/run-imapsync \
    && chmod +x /usr/local/bin/run-imapsync

RUN printf '%s\n' \
    '#!/bin/sh' \
    'set -eu' \
    '' \
    'while true; do' \
    '  start="$(date +%s)"' \
    '  /usr/local/bin/run-imapsync' \
    '  end="$(date +%s)"' \
    '  elapsed=$((end - start))' \
    '  sleep_for=$((180 - elapsed))' \
    '' \
    '  if [ "$sleep_for" -gt 0 ]; then' \
    '    sleep "$sleep_for"' \
    '  fi' \
    'done' \
    > /usr/local/bin/run-loop \
    && chmod +x /usr/local/bin/run-loop

CMD ["/usr/local/bin/run-loop"]

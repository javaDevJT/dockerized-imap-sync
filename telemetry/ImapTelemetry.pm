package ImapTelemetry;

use strict;
use warnings;

use Fcntl qw(F_GETFD F_SETFD FD_CLOEXEC);
use Hash::Util::FieldHash qw(fieldhash);
use JSON::PP ();
use Scalar::Util qw(refaddr);
use Time::HiRes qw(clock_gettime CLOCK_REALTIME CLOCK_MONOTONIC);

my $EVENT_FH;
my $JSON = JSON::PP->new->canonical->utf8;
our $ACTIVE;
our $CONNECT_ACTIVE;

fieldhash my %PENDING;
fieldhash my %TLS_ESTABLISHED;

sub _open_event_writer {
    my $fd = $ENV{IMAP_TELEMETRY_FD};
    return 0 unless defined($fd) && $fd =~ /\A[0-9]{1,10}\z/;
    return 0 if $fd < 3 || $fd > 2_147_483_647;

    my $fh;
    my $opened;
    {
        local $@;
        local $!;
        $opened = eval { open($fh, ">&=$fd") ? 1 : 0 };
    }
    return 0 unless $opened;

    # Keep the event pipe out of any subprocesses launched by the IMAP client.
    {
        local $@;
        local $!;
        eval {
            my $flags = fcntl($fh, F_GETFD, 0);
            fcntl($fh, F_SETFD, $flags | FD_CLOEXEC) if defined $flags;
        };
    }

    binmode($fh);
    $EVENT_FH = $fh;
    return 1;
}

sub _clock_pair {
    my ($wall, $mono);
    {
        local $@;
        local $!;
        eval {
            $wall = clock_gettime(CLOCK_REALTIME);
            $mono = clock_gettime(CLOCK_MONOTONIC);
        };
    }
    $wall = Time::HiRes::time() unless defined $wall;
    $mono = $wall unless defined $mono;
    return (int($wall * 1_000_000_000), $mono);
}

sub _read_accessor {
    my ($self, $name) = @_;
    my $value;
    {
        local $@;
        local $!;
        eval { $value = $self->$name() };
    }
    return $value;
}

sub _host_key {
    my ($value) = @_;
    return unless defined($value) && !ref($value);
    $value = "$value";
    $value =~ s/^\s+|\s+$//g;
    $value =~ s/\.$//;
    return unless length($value) <= 255;
    return unless $value =~ /\A[A-Za-z0-9._:\[\]%-]+\z/;
    return lc $value;
}

sub _server_address {
    my ($self) = @_;
    my $server = _read_accessor($self, 'Server');
    my $key = _host_key($server);
    return defined($key) ? $key : 'unknown';
}

sub _server_role {
    my ($self) = @_;
    my $server = _host_key(_read_accessor($self, 'Server'));
    return 'unknown' unless defined $server;

    my $source = _host_key($ENV{IMAP1_HOST});
    my $destination = _host_key($ENV{IMAP2_HOST});
    my $is_source = defined($source) && $server eq $source;
    my $is_destination = defined($destination) && $server eq $destination;

    return 'source' if $is_source && !$is_destination;
    return 'destination' if $is_destination && !$is_source;
    return 'unknown';
}

sub _server_port {
    my ($self) = @_;
    my $port = _read_accessor($self, 'Port');
    if (!defined($port) || ref($port) || $port !~ /\A[0-9]{1,5}\z/ || $port < 1 || $port > 65535) {
        my $ssl = _read_accessor($self, 'Ssl');
        $port = $ssl ? 993 : 143;
    }
    return int($port);
}

sub _tls_enabled {
    my ($self) = @_;
    return 1 if $TLS_ESTABLISHED{$self};
    return _read_accessor($self, 'Ssl') ? 1 : 0;
}

sub _command_name {
    my ($opt, $raw) = @_;
    return 'OTHER' unless defined($raw) && !ref($raw);
    my $text = "$raw";

    if (ref($opt) eq 'HASH' && exists($opt->{addtag}) && !$opt->{addtag} && defined($opt->{tag})) {
        my $tag = "$opt->{tag}";
        $text =~ s/\A\s*\Q$tag\E\s+//i;
    }

    my ($verb, $subverb) = $text =~ /\A\s*([A-Za-z][A-Za-z0-9_-]*)(?:\s+([A-Za-z][A-Za-z0-9_-]*))?/;
    return 'OTHER' unless defined $verb;
    $verb = uc $verb;
    $subverb = uc($subverb // '');

    if ($verb eq 'UID') {
        return "UID $subverb" if $subverb =~ /\A(?:COPY|EXPUNGE|FETCH|MOVE|SEARCH|SORT|STORE|THREAD)\z/;
        return 'OTHER';
    }

    return $verb if $verb =~ /\A(?:APPEND|AUTHENTICATE|CAPABILITY|CHECK|CLOSE|COMPRESS|CONNECT|COPY|CREATE|DELETE|DELETEACL|ENABLE|EXAMINE|EXPUNGE|FETCH|GETACL|GETQUOTA|GETQUOTAROOT|ID|IDLE|LIST|LISTRIGHTS|LOGIN|LOGOUT|LSUB|MOVE|MYRIGHTS|NAMESPACE|NOOP|NOTIFY|RENAME|SEARCH|SELECT|SETACL|SETQUOTA|SORT|STARTTLS|STATUS|STORE|SUBSCRIBE|THREAD|UNSELECT|UNSUBSCRIBE|XLIST)\z/;
    return 'OTHER';
}

sub _new_context {
    my ($self, $opt, $raw) = @_;
    my ($start_ns, $start_mono) = _clock_pair();
    return {
        start_ns    => $start_ns,
        start_mono  => $start_mono,
        object_id   => refaddr($self),
        command     => _command_name($opt, $raw),
        server_role => _server_role($self),
        server_address => _server_address($self),
        server_port => _server_port($self),
        tls         => _tls_enabled($self) ? JSON::PP::true : JSON::PP::false,
        status      => undef,
        complete    => 0,
        attempted   => 0,
    };
}

sub _status_name {
    my ($status) = @_;
    return 'ERROR' unless defined($status) && !ref($status);
    $status = uc "$status";
    return $status if $status =~ /\A(?:OK|NO|BAD|BYE)\z/;
    return undef if $status eq '+';
    return 'UNKNOWN';
}

sub _invoke_original {
    my ($original, $want, $args, $entry_at, $entry_errno) = @_;
    my ($ok, $scalar_result, @list_result, $return_at, $return_errno);
    $ok = eval {
        $@ = $entry_at;
        $! = $entry_errno;
        if ($want) {
            @list_result = $original->(@$args);
        }
        elsif (defined $want) {
            $scalar_result = $original->(@$args);
        }
        else {
            $original->(@$args);
        }
        $return_at = $@;
        $return_errno = $!;
        1;
    };

    my $call = {
        ok             => $ok ? 1 : 0,
        scalar_result  => $scalar_result,
        list_result    => \@list_result,
        return_at      => $return_at,
        return_errno   => $return_errno,
    };
    if (!$ok) {
        $call->{exception} = $@;
        $call->{exception_errno} = $!;
    }
    return $call;
}

sub _restore_call_state {
    my ($call) = @_;
    if (!$call->{ok}) {
        $@ = $call->{exception};
        $! = $call->{exception_errno};
        die;
    }
    $@ = $call->{return_at};
    $! = $call->{return_errno};
    return;
}

sub _call_succeeded {
    my ($call, $want) = @_;
    return @{$call->{list_result}} && $call->{list_result}[0] if $want;
    return $call->{scalar_result} if defined $want;
    return undef;
}

sub _return_call {
    my ($call, $want) = @_;
    return @{$call->{list_result}} if $want;
    return $call->{scalar_result} if defined $want;
    return;
}

sub _finish_pending {
    my ($self, $expected_command, $status) = @_;
    my $ctx = $PENDING{$self} or return 0;
    return 0 if defined($expected_command) && $ctx->{command} ne $expected_command;

    delete $PENDING{$self};
    $ctx->{status} = $status if defined $status;
    $ctx->{status} ||= 'ERROR';
    $ctx->{complete} = 1;
    _emit_context($ctx, 0);
    return 1;
}

sub _response_status_from_lines {
    my ($self, $output) = @_;
    my $ctx = $PENDING{$self} or return;
    return unless $ctx->{command} eq 'AUTHENTICATE';
    return unless defined($ctx->{response_tag}) && ref($output) eq 'ARRAY';

    my $tag = $ctx->{response_tag};
    my $status;
    for my $line (@$output) {
        next unless ref($line) eq 'ARRAY' && defined($line->[2]) && !ref($line->[2]);
        my $text = "$line->[2]";
        if ($text =~ /\A\Q$tag\E\s+(OK|NO|BAD)\b/i) {
            $status = uc $1;
            last;
        }
        if ($text =~ /\A\*\s+BYE\b/i) {
            $status = 'BYE';
            last;
        }
    }
    _finish_pending($self, 'AUTHENTICATE', $status) if defined $status;
}

sub _is_idle_done {
    my ($opt, $raw) = @_;
    return 0 unless defined($raw) && !ref($raw) && "$raw" =~ /\A\s*DONE\s*\z/i;
    return 0 unless ref($opt) eq 'HASH' && exists($opt->{addtag}) && !$opt->{addtag};
    return ref($opt->{tag}) eq 'Regexp' ? 1 : 0;
}

sub _response_received {
    my ($self, $status, $tag) = @_;
    my $id = refaddr($self);
    my $name = _status_name($status);

    if ($ACTIVE && defined($ACTIVE->{object_id}) && $ACTIVE->{object_id} == $id) {
        if (!defined $name) {
            $ACTIVE->{continued} = 1;
            if ($ACTIVE->{command} eq 'AUTHENTICATE'
                && defined($tag)
                && !ref($tag)) {
                $ACTIVE->{response_tag} = "$tag";
            }
        }
        else {
            $ACTIVE->{status} = $name;
            $ACTIVE->{complete} = 1;
        }
        return;
    }

    # The initial server greeting is read by Socket() with a '*' tag, outside
    # the normal command wrapper. Capture only that greeting for CONNECT.
    if ($CONNECT_ACTIVE
        && defined($CONNECT_ACTIVE->{object_id})
        && $CONNECT_ACTIVE->{object_id} == $id
        && defined($tag)
        && !ref($tag)
        && "$tag" eq '*') {
        my $greeting = defined($status) && !ref($status) && uc("$status") eq 'PREAUTH'
            ? 'OK'
            : $name;
        $CONNECT_ACTIVE->{status} = defined($greeting) ? $greeting : 'UNKNOWN';
        $CONNECT_ACTIVE->{complete} = 1;
        return;
    }

    my $pending = $PENDING{$self} or return;
    if (defined($tag) && !ref($tag) && "$tag" eq '*') {
        if ($pending->{command} eq 'IDLE' && defined($name) && $name eq 'BYE') {
            _finish_pending($self, 'IDLE', 'BYE');
        }
        return;
    }
    if (defined($pending->{response_tag}) && defined($tag) && !ref($tag)) {
        return unless "$tag" eq $pending->{response_tag};
    }
    if (!defined $name) {
        $pending->{continued} = 1;
        return;
    }
    $pending->{status} = $name;
    $pending->{complete} = 1;
    delete $PENDING{$self};
    _emit_context($pending, 0);
}

sub _emit_context {
    my ($ctx, $failed) = @_;
    return unless $EVENT_FH && $ctx && $ctx->{attempted};

    my $status = $ctx->{status};
    $status = 'ERROR' if $failed;
    $status ||= 'UNKNOWN';

    my ($end_clock_ns, $end_mono) = _clock_pair();
    my $elapsed = $end_mono - $ctx->{start_mono};
    $elapsed = 0 if $elapsed < 0;
    my $end_ns = $ctx->{start_ns} + int($elapsed * 1_000_000_000);
    $end_ns = $ctx->{start_ns} if $end_ns < $ctx->{start_ns};

    my $expected_logout_bye = $ctx->{command} eq 'LOGOUT' && $status eq 'BYE';
    my $error = ($status eq 'OK' || $expected_logout_bye) ? JSON::PP::false : JSON::PP::true;
    my $record = {
        event           => 'imap.request',
        command         => $ctx->{command},
        server_role     => $ctx->{server_role},
        server_address  => $ctx->{server_address},
        server_port     => $ctx->{server_port},
        tls             => $ctx->{tls},
        start_ns        => $ctx->{start_ns},
        end_ns          => $end_ns,
        response_status => $status,
        error           => $error,
    };

    my $line;
    {
        local $@;
        local $!;
        local $SIG{PIPE} = 'IGNORE';
        eval { $line = $JSON->encode($record) . "\n" };
        return unless defined($line) && length($line) <= 1024;

        my $offset = 0;
        while ($offset < length($line)) {
            my $chunk = substr($line, $offset);
            my $written = syswrite($EVENT_FH, $chunk);
            return unless defined($written) && $written > 0;
            $offset += $written;
        }
    }
}

sub _install_hooks {
    require Mail::IMAPClient;

    no warnings qw(redefine once);
    my $original_send_line = \&Mail::IMAPClient::_send_line;
    my $original_send_bytes = \&Mail::IMAPClient::_send_bytes;
    my $original_get_response = \&Mail::IMAPClient::_get_response;
    my $original_read_line = \&Mail::IMAPClient::_read_line;
    my $original_command_do = \&Mail::IMAPClient::_imap_command_do;
    my $original_connect = \&Mail::IMAPClient::connect;
    my $original_starttls = \&Mail::IMAPClient::starttls;

    *Mail::IMAPClient::_send_line = sub {
        my $want = wantarray;
        my @args = @_;
        my $self = $_[0];
        my $active = $ACTIVE
            && defined($ACTIVE->{object_id})
            && $ACTIVE->{object_id} == refaddr($self)
            ? $ACTIVE
            : undef;
        $active->{attempted} = 1 if $active;
        my $pending = $PENDING{$self};
        my $auth_pending = !$active
            && $pending
            && $pending->{command} eq 'AUTHENTICATE'
            ? $pending
            : undef;

        my $call = _invoke_original($original_send_line, $want, \@args, $@, $!);
        if (!$call->{ok}) {
            _finish_pending($self, 'AUTHENTICATE', 'ERROR') if $auth_pending;
            _restore_call_state($call);
        }

        if ($auth_pending && defined($want) && !_call_succeeded($call, $want)) {
            _finish_pending($self, 'AUTHENTICATE', 'ERROR');
        }
        _restore_call_state($call);
        return _return_call($call, $want);
    };

    *Mail::IMAPClient::_send_bytes = sub ($) {
        my $want = wantarray;
        my @args = @_;
        my $self = $args[0];
        my $call = _invoke_original($original_send_bytes, $want, \@args, $@, $!);
        if (!$call->{ok} || (defined($want) && !_call_succeeded($call, $want))) {
            _finish_pending($self, 'APPEND', 'ERROR');
        }
        _restore_call_state($call);
        return _return_call($call, $want);
    };

    *Mail::IMAPClient::_get_response = sub {
        my $want = wantarray;
        my @args = @_;
        my $tag_index = @args > 1 && ref($args[1]) eq 'HASH' ? 2 : 1;
        my $tag = @args > $tag_index ? $args[$tag_index] : undef;
        if ($want) {
            my @result = $original_get_response->(@args);
            _response_received($args[0], $result[0], $tag);
            return @result;
        }
        if (defined $want) {
            my $result = $original_get_response->(@args);
            _response_received($args[0], $result, $tag);
            return $result;
        }
        $original_get_response->(@args);
        _response_received($args[0], undef, $tag);
        return;
    };

    *Mail::IMAPClient::_read_line = sub {
        my $want = wantarray;
        my @args = @_;
        my $self = $args[0];
        my $call = _invoke_original($original_read_line, $want, \@args, $@, $!);
        if (!$call->{ok}) {
            _finish_pending($self, 'AUTHENTICATE', 'ERROR')
                if $PENDING{$self} && $PENDING{$self}{command} eq 'AUTHENTICATE';
            _restore_call_state($call);
        }

        my $output = $want
            ? (@{$call->{list_result}} ? $call->{list_result}[0] : undef)
            : $call->{scalar_result};
        if ($PENDING{$self} && $PENDING{$self}{command} eq 'AUTHENTICATE') {
            if (defined $output) {
                _response_status_from_lines($self, $output);
            }
            else {
                _finish_pending($self, 'AUTHENTICATE', 'ERROR');
            }
        }

        _restore_call_state($call);
        return _return_call($call, $want);
    };

    *Mail::IMAPClient::connect = sub (@) {
        my $want = wantarray;
        my @args = @_;
        my $self = $args[0];
        my $ctx = _new_context($self, {}, 'CONNECT');
        my $before_at = $@;
        my $before_errno = $!;
        my ($return_at, $return_errno);
        my ($ok, $scalar_result, @list_result);

        # connect() opens the socket and consumes the server greeting. Treat
        # that bounded operation as a CLIENT span, even when TCP/TLS fails
        # before a protocol command can be sent.
        $ctx->{attempted} = 1;
        {
            local $CONNECT_ACTIVE = $ctx;
            $ok = eval {
                $@ = $before_at;
                $! = $before_errno;
                if ($want) {
                    @list_result = $original_connect->(@args);
                }
                elsif (defined $want) {
                    $scalar_result = $original_connect->(@args);
                }
                else {
                    $original_connect->(@args);
                }
                $return_at = $@;
                $return_errno = $!;
                1;
            };
        }

        if (!$ok) {
            my $exception = $@;
            $ctx->{status} = 'ERROR';
            $ctx->{tls} = _tls_enabled($self) ? JSON::PP::true : JSON::PP::false;
            _emit_context($ctx, 1);
            $@ = $exception;
            die;
        }

        my $success = $want
            ? (@list_result && $list_result[0])
            : defined($want)
                ? $scalar_result
                : undef;
        if (defined($want) && !$success && (!$ctx->{status} || $ctx->{status} eq 'OK')) {
            # The greeting may have succeeded even if later TLS or automatic
            # LOGIN setup made connect() fail.
            $ctx->{status} = 'ERROR';
        }
        $ctx->{tls} = _tls_enabled($self) ? JSON::PP::true : JSON::PP::false;
        $ctx->{status} ||= defined($want) && $success ? 'UNKNOWN' : 'ERROR';
        _emit_context($ctx, 0);

        $@ = $return_at;
        $! = $return_errno;

        return @list_result if $want;
        return $scalar_result if defined $want;
        return;
    };

    *Mail::IMAPClient::_imap_command_do = sub {
        my $want = wantarray;
        my @args = @_;
        my $self = $args[0];
        my @command_args = @args[1 .. $#args];
        my $opt = @command_args && ref($command_args[0]) eq 'HASH' ? $command_args[0] : {};
        my $raw = $command_args[0] && ref($command_args[0]) eq 'HASH' ? $command_args[1] : $command_args[0];

        my $ctx;
        if (exists $PENDING{$self}) {
            my $pending = $PENDING{$self};
            if ($pending->{command} eq 'IDLE' && _is_idle_done($opt, $raw)) {
                $ctx = delete $PENDING{$self};
                $ctx->{continued} = 0;
                $ctx->{complete} = 0;
                $ctx->{status} = undef;
            }
            else {
                delete $PENDING{$self};
                _emit_context($pending, 1);
            }
        }

        $ctx ||= _new_context($self, $opt, $raw);
        my $before_at = $@;
        my $before_errno = $!;
        my ($return_at, $return_errno);
        my ($ok, $scalar_result, @list_result);
        {
            local $ACTIVE = $ctx;
            $ok = eval {
                $@ = $before_at;
                $! = $before_errno;
                if ($want) {
                    @list_result = $original_command_do->(@args);
                }
                elsif (defined $want) {
                    $scalar_result = $original_command_do->(@args);
                }
                else {
                    $original_command_do->(@args);
                }
                $return_at = $@;
                $return_errno = $!;
                1;
            };
        }

        if (!$ok) {
            my $exception = $@;
            $ctx->{status} = 'ERROR';
            _emit_context($ctx, 1);
            $@ = $exception;
            die;
        }

        if ($ctx->{attempted}) {
            if ($ctx->{continued} && !$ctx->{complete}) {
                $PENDING{$self} = $ctx;
            }
            else {
                if (!$ctx->{status}) {
                    my $success = $want ? (@list_result && $list_result[0]) : $scalar_result;
                    $ctx->{status} = defined($want) && !$success ? 'ERROR' : 'UNKNOWN';
                }
                _emit_context($ctx, 0);
            }
        }

        $@ = $return_at;
        $! = $return_errno;

        return @list_result if $want;
        return $scalar_result if defined $want;
        return;
    };

    *Mail::IMAPClient::starttls = sub {
        my $want = wantarray;
        my @args = @_;
        my $self = $args[0];
        delete $TLS_ESTABLISHED{$self};

        if ($want) {
            my @result = $original_starttls->(@args);
            $TLS_ESTABLISHED{$self} = 1 if @result && $result[0];
            return @result;
        }
        if (defined $want) {
            my $result = $original_starttls->(@args);
            $TLS_ESTABLISHED{$self} = 1 if $result;
            return $result;
        }
        $original_starttls->(@args);
        return;
    };
}

if (_open_event_writer()) {
    _install_hooks();
}

1;

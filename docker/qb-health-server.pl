#!/usr/bin/perl
use strict;
use warnings;
use IO::Select;
use IO::Socket::INET;

my $mode = shift // "--serve";

sub source_commit {
    my $commit = $ENV{QB_SOURCE_COMMIT} // "";
    return $commit =~ /\A[a-fA-F0-9]{40}\z/ ? $commit : "unknown";
}

sub runtime_healthy {
    my ($run_loop_pid) = @_;
    return 0 unless defined($run_loop_pid) && $run_loop_pid =~ /\A[1-9][0-9]*\z/;
    return 0 unless kill(0, $run_loop_pid);
    return 0 unless -x "/usr/local/bin/imapsync";
    return 0 unless system("/bin/bash", "-n", "/usr/local/bin/run-imapsync") == 0;
    return 0 unless system("/bin/bash", "-n", "/usr/local/bin/run-loop") == 0;

    for my $name (qw(IMAP1_HOST IMAP1_USER IMAP1_PASSWORD IMAP2_HOST IMAP2_USER IMAP2_PASSWORD)) {
        my $value = $ENV{$name};
        return 0 unless defined($value) && length($value) && $value !~ /[\r\n]/;
    }
    return 0 if $ENV{IMAP1_HOST} =~ /\s/ || $ENV{IMAP2_HOST} =~ /\s/;
    return 1;
}

sub probe {
    my $socket = IO::Socket::INET->new(
        PeerAddr => "127.0.0.1",
        PeerPort => 8080,
        Proto => "tcp",
        Timeout => 2,
    ) or die "health endpoint unavailable\n";
    print {$socket} "GET /healthz HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n";
    my $response = do { local $/; <$socket> // "" };
    close $socket;
    my ($headers, $body) = split /\r?\n\r?\n/, $response, 2;
    $body //= "";
    $body =~ s/\s+\z//;
    print "$body\n" if length $body;
    exit 0 if $headers =~ /\AHTTP\/1\.[01] 200\b/ && $body =~ /"sourceCommit":"(?:[a-fA-F0-9]{40}|unknown)"/;
    exit 1;
}

if ($mode eq "--source-commit") {
    print source_commit(), "\n";
    exit 0;
}

if ($mode eq "--probe") {
    probe();
}

die "usage: qb-health-server --serve RUN_LOOP_PID | --probe | --source-commit\n"
    unless $mode eq "--serve";
my $run_loop_pid = shift // "";
my $server = IO::Socket::INET->new(
    LocalAddr => "0.0.0.0",
    LocalPort => 8080,
    Proto => "tcp",
    Listen => 16,
    ReuseAddr => 1,
) or die "cannot listen on port 8080: $!\n";

$SIG{TERM} = sub { close $server; exit 0 };
$SIG{INT} = sub { close $server; exit 0 };

while (my $client = $server->accept()) {
    my $selector = IO::Select->new($client);
    my $request = "";
    if ($selector->can_read(2)) {
        sysread($client, $request, 4096);
    }

    my $is_health = $request =~ /\A(?:GET|HEAD) \/healthz(?:\?[^\s]*)? HTTP\/1\.[01]\r?\n/;
    my $healthy = $is_health && runtime_healthy($run_loop_pid);
    my $status_code = $healthy ? 200 : ($is_health ? 503 : 404);
    my $reason = $healthy ? "OK" : ($is_health ? "Service Unavailable" : "Not Found");
    my $status = $healthy ? "ok" : "unhealthy";
    my $body = '{"status":"' . $status . '","sourceCommit":"' . source_commit() . '"}';
    my $head = $request =~ /\AHEAD /;
    print {$client} "HTTP/1.1 $status_code $reason\r\n",
        "Content-Type: application/json; charset=utf-8\r\n",
        "Cache-Control: no-store\r\n",
        "Connection: close\r\n",
        "Content-Length: ", length($body), "\r\n\r\n";
    print {$client} $body unless $head;
    close $client;
}

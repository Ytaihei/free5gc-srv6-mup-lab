package main

import (
	"context"
	"flag"
	"fmt"
	"log"
	"net"
	"net/http"
	"os"
	"os/signal"
	"syscall"
	"time"

	"github.com/Ytaihei/free5gc-srv6-mup-lab/api/mup/v1/mupv1connect"
	"github.com/Ytaihei/free5gc-srv6-mup-lab/internal/dashboard"
)

func main() {
	listen := flag.String("listen", envOr("MUP_DASHBOARD_LISTEN", "127.0.0.1:8787"), "HTTP listen address")
	tailnetPort := flag.Int("tailnet-port", 0, "also listen on the tailscale0 IPv4 address at this port")
	tailnetInterface := flag.String("tailnet-interface", envOr("MUP_TAILNET_INTERFACE", "tailscale0"), "Tailscale interface")
	controllerURL := flag.String("controller", envOr("MUP_CONTROLLER", "http://192.168.123.14:9443"), "MUP-C base URL")
	sshUser := flag.String("ssh-user", envOr("MUP_SSH_USER", "ubuntu"), "lab SSH user")
	sshKey := flag.String("ssh-key", envOr("MUP_SSH_KEY", ""), "lab SSH private key")
	interval := flag.Duration("interval", 5*time.Second, "state collection interval")
	stateFile := flag.String("state-file", "", "read-only collector snapshot (disables SSH/controller collection)")
	socket := flag.String("socket", "", "serve only on this Unix socket, with no TCP listener")
	flag.Parse()
	if *interval < time.Second || *interval%time.Second != 0 {
		log.Fatal("interval must be a positive whole number of seconds")
	}

	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	var provider dashboard.StateProvider
	if *stateFile != "" {
		provider = dashboard.FileProvider{Path: *stateFile}
	} else {
		client := mupv1connect.NewControllerServiceClient(&http.Client{Timeout: 4 * time.Second}, *controllerURL)
		collectorConfig := dashboard.CollectorConfig{
			CoreName:        envOr("MUP_LAB_CORE_NAME", "lab-core"),
			RANName:         envOr("MUP_LAB_RAN_NAME", "lab-ran"),
			TPEName:         envOr("MUP_LAB_TPE_NAME", "lab-tpe"),
			NPEName:         envOr("MUP_LAB_NPE_NAME", "lab-npe"),
			MUPCName:        envOr("MUP_LAB_MUPC_NAME", "lab-mupc"),
			DNName:          envOr("MUP_LAB_DN_NAME", "lab-dn"),
			CoreAddress:     envOr("MUP_LAB_CORE", "192.168.123.10"),
			RANAddress:      envOr("MUP_LAB_RAN", "192.168.123.11"),
			TPEAddress:      envOr("MUP_LAB_TPE", "192.168.123.12"),
			NPEAddress:      envOr("MUP_LAB_NPE", "192.168.123.13"),
			MUPCAddress:     envOr("MUP_LAB_MUPC", "192.168.123.14"),
			DNAddress:       envOr("MUP_LAB_DN", "192.168.123.15"),
			DNTarget:        envOr("MUP_DN_TARGET", "10.210.6.15"),
			IntervalSeconds: int(*interval / time.Second),
		}
		collector := dashboard.NewCollectorWithConfig(client, dashboard.SSHRunner{User: *sshUser, IdentityFile: *sshKey}, collectorConfig)
		monitor := dashboard.NewMonitor(collector)
		go monitor.Run(ctx, *interval)
		provider = monitor
	}
	if *socket != "" {
		if *tailnetPort != 0 {
			log.Fatal("--socket cannot be combined with --tailnet-port")
		}
		listener, err := unixListener(*socket)
		if err != nil {
			log.Fatal(err)
		}
		defer listener.Close()
		server := &http.Server{Handler: dashboard.Handler(provider), ReadHeaderTimeout: 5 * time.Second}
		go func() { <-ctx.Done(); _ = server.Close() }()
		log.Printf("SRv6 MUP dashboard listening on Unix socket %s", *socket)
		if err := server.Serve(listener); err != nil && err != http.ErrServerClosed {
			log.Fatal(err)
		}
		return
	}

	addresses := []string{*listen}
	if *tailnetPort > 0 {
		address, err := tailnetAddress(*tailnetInterface, *tailnetPort)
		if err != nil {
			log.Fatalf("resolve Tailscale address: %v", err)
		}
		if address != *listen {
			addresses = append(addresses, address)
		}
	}
	servers := make([]*http.Server, 0, len(addresses))
	errors := make(chan error, len(addresses))
	for _, address := range addresses {
		server := &http.Server{Addr: address, Handler: dashboard.Handler(provider), ReadHeaderTimeout: 5 * time.Second}
		servers = append(servers, server)
		go func() {
			log.Printf("SRv6 MUP dashboard listening on http://%s", address)
			errors <- server.ListenAndServe()
		}()
	}
	go func() {
		<-ctx.Done()
		shutdown, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer cancel()
		for _, server := range servers {
			_ = server.Shutdown(shutdown)
		}
	}()
	if err := <-errors; err != nil && err != http.ErrServerClosed {
		log.Fatal(err)
	}
}

func unixListener(path string) (net.Listener, error) {
	if info, err := os.Lstat(path); err == nil {
		if info.Mode()&os.ModeSocket == 0 {
			return nil, fmt.Errorf("refusing non-socket path")
		}
		connection, dialErr := net.DialTimeout("unix", path, time.Second)
		if dialErr == nil {
			connection.Close()
			return nil, fmt.Errorf("socket already in use")
		}
		if err := os.Remove(path); err != nil {
			return nil, err
		}
	} else if !os.IsNotExist(err) {
		return nil, err
	}
	listener, err := net.Listen("unix", path)
	if err != nil {
		return nil, err
	}
	// The directory is owned by the unprivileged web UID. Guest SSH users may
	// connect to this read-only API; it has no command or mutation endpoints.
	if err := os.Chmod(path, 0666); err != nil {
		listener.Close()
		return nil, err
	}
	return listener, nil
}

func tailnetAddress(interfaceName string, port int) (string, error) {
	iface, err := net.InterfaceByName(interfaceName)
	if err != nil {
		return "", err
	}
	addresses, err := iface.Addrs()
	if err != nil {
		return "", err
	}
	for _, address := range addresses {
		ip, _, err := net.ParseCIDR(address.String())
		if err == nil && ip.To4() != nil {
			return net.JoinHostPort(ip.String(), fmt.Sprint(port)), nil
		}
	}
	return "", fmt.Errorf("%s has no IPv4 address", interfaceName)
}

func envOr(name, fallback string) string {
	if value := os.Getenv(name); value != "" {
		return value
	}
	return fallback
}

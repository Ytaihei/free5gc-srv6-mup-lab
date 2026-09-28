package main

import (
	"context"
	"flag"
	"fmt"
	"net/http"
	"os"
	"strings"
	"time"

	"connectrpc.com/connect"
	"google.golang.org/protobuf/encoding/protojson"
	"google.golang.org/protobuf/proto"
	"google.golang.org/protobuf/types/known/emptypb"

	mupv1 "github.com/Ytaihei/free5gc-srv6-mup-lab/api/mup/v1"
	"github.com/Ytaihei/free5gc-srv6-mup-lab/api/mup/v1/mupv1connect"
)

func main() {
	server := flag.String("server", envOr("MUP_CONTROLLER", ""), "controller base URL (otherwise read installed config)")
	configPath := flag.String("server-file", "/etc/srv6-mup-controller-url", "installed controller URL file")
	timeout := flag.Duration("timeout", 5*time.Second, "request timeout")
	flag.Parse()
	if *server == "" {
		address, err := os.ReadFile(*configPath)
		if err != nil {
			fmt.Fprintln(os.Stderr, "read controller address (or specify --server):", err)
			os.Exit(1)
		}
		*server = strings.TrimSpace(string(address))
	}
	args := flag.Args()
	if len(args) == 0 {
		usage()
	}
	ctx, cancel := context.WithTimeout(context.Background(), *timeout)
	defer cancel()
	client := mupv1connect.NewControllerServiceClient(&http.Client{Timeout: *timeout}, *server)
	var out proto.Message
	var err error
	switch args[0] {
	case "status":
		var r *connect.Response[mupv1.StatusResponse]
		r, err = client.Status(ctx, connect.NewRequest(&mupv1.StatusRequest{}))
		if r != nil {
			out = r.Msg
		}
	case "sessions":
		var r *connect.Response[mupv1.ListSessionsResponse]
		r, err = client.ListSessions(ctx, connect.NewRequest(&mupv1.ListSessionsRequest{}))
		if r != nil {
			out = r.Msg
		}
	case "suppress", "resume":
		if len(args) != 2 {
			usage()
		}
		var r *connect.Response[mupv1.SetSuppressionResponse]
		r, err = client.SetSuppression(ctx, connect.NewRequest(&mupv1.SetSuppressionRequest{Key: args[1], Suppressed: args[0] == "suppress"}))
		if r != nil {
			out = r.Msg
		}
	case "reconcile":
		var r *connect.Response[mupv1.ReconcileResponse]
		r, err = client.Reconcile(ctx, connect.NewRequest(&emptypb.Empty{}))
		if r != nil {
			out = r.Msg
		}
	default:
		usage()
	}
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
	b, err := protojson.MarshalOptions{
		Multiline: true, Indent: "  ", UseProtoNames: true, EmitUnpopulated: true,
	}.Marshal(out)
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
	fmt.Println(string(b))
}

func envOr(name, fallback string) string {
	if v := os.Getenv(name); v != "" {
		return v
	}
	return fallback
}

func usage() {
	fmt.Fprintln(os.Stderr, "usage: mupctl [--server URL] status|sessions|suppress KEY|resume KEY|reconcile")
	os.Exit(2)
}

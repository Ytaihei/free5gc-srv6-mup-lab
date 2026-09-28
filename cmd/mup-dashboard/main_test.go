package main

import (
	"net"
	"os"
	"path/filepath"
	"testing"
)

func TestUnixListenerRefusesFilesSymlinksAndLiveSockets(t *testing.T) {
	root := t.TempDir()
	file := filepath.Join(root, "file")
	if err := os.WriteFile(file, []byte("preserved"), 0600); err != nil {
		t.Fatal(err)
	}
	link := filepath.Join(root, "link")
	if err := os.Symlink(file, link); err != nil {
		t.Fatal(err)
	}
	for _, path := range []string{file, link} {
		if listener, err := unixListener(path); err == nil {
			listener.Close()
			t.Fatal("unsafe path accepted")
		}
	}
	path := filepath.Join(root, "http.sock")
	listener, err := unixListener(path)
	if err != nil {
		t.Fatal(err)
	}
	defer listener.Close()
	if other, err := unixListener(path); err == nil {
		other.Close()
		t.Fatal("live socket replaced")
	}
	info, err := os.Stat(path)
	if err != nil || info.Mode().Perm() != 0666 {
		t.Fatal("unexpected socket mode")
	}
	listener.(*net.UnixListener).SetUnlinkOnClose(false)
	listener.Close()
	restarted, err := unixListener(path)
	if err != nil {
		t.Fatal(err)
	}
	restarted.Close()
}

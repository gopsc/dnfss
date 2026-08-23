package gomsg

import (
	"bytes"
	"encoding/binary"
	"errors"
	"io"
	"testing"
)

func TestEncodeRoundTrip(t *testing.T) {
	payload := []byte(`{"hello":"世界"}`)
	frame, err := Encode(payload)
	if err != nil {
		t.Fatal(err)
	}
	if len(frame) != 4+len(payload) {
		t.Fatalf("frame length = %d, want %d", len(frame), 4+len(payload))
	}
	if got := binary.BigEndian.Uint32(frame[:4]); got != uint32(len(payload)) {
		t.Fatalf("header = %d, want %d", got, len(payload))
	}
	got, err := ReadFrame(bytes.NewReader(frame), 0)
	if err != nil {
		t.Fatal(err)
	}
	if !bytes.Equal(got, payload) {
		t.Fatalf("payload mismatch: %q", got)
	}
}

func TestEncodeTooLarge(t *testing.T) {
	payload := make([]byte, MaxMsgSize+1)
	if _, err := Encode(payload); err == nil {
		t.Fatal("expected error for oversized payload")
	}
}

func TestReadFrameTooLarge(t *testing.T) {
	header := make([]byte, 4)
	binary.BigEndian.PutUint32(header, MaxMsgSize+1)
	_, err := ReadFrame(bytes.NewReader(header), 0)
	if err == nil {
		t.Fatal("expected error for oversized header")
	}
}

func TestReadFrameTruncated(t *testing.T) {
	frame, _ := Encode([]byte("abcdef"))
	if _, err := ReadFrame(bytes.NewReader(frame[:len(frame)-3]), 0); err == nil {
		t.Fatal("expected error for truncated payload")
	}
}

func TestReadFrameMaxParam(t *testing.T) {
	frame, _ := Encode([]byte("hello"))
	if _, err := ReadFrame(bytes.NewReader(frame), 4); err == nil {
		t.Fatal("expected error when max < payload length")
	}
}

func TestReadFrameEOF(t *testing.T) {
	if _, err := ReadFrame(bytes.NewReader(nil), 0); !errors.Is(err, io.EOF) {
		t.Fatalf("expected EOF, got %v", err)
	}
}

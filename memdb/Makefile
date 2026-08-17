CPP      = g++
CPPFLAGS = -std=c++20 -O3 -Wall -Werror -pipe
INCS     = -I ../libmsg/inc -I ../libmemdb/inc
LIBS     = -L ../libmsg/build -lus_msg -lboost_json -lboost_program_options -lpthread
RPATH    = -Wl,-rpath,'$$ORIGIN/../../libmsg/build'

BUILD    = build
TARGET   = $(BUILD)/memdb
SRC      = src/memdb.cpp

HOST    ?= 127.0.0.1
PORT    ?= 8011

all: $(TARGET)

$(BUILD):
	mkdir -p $(BUILD)

$(TARGET): $(SRC) | $(BUILD)
	$(CPP) $(CPPFLAGS) $(INCS) -o $@ $< $(LIBS) $(RPATH)

# ── libmemdb 单元测试 ────────────────────────────────────────────────────

TEST_LIBMEMDB = test_libmemdb
TEST_CPPFLAGS  = -std=c++20 -O0 -g -Wall -Werror

$(TEST_LIBMEMDB): test_libmemdb.cpp
	$(CPP) $(TEST_CPPFLAGS) -I ../libmemdb/inc -o $@ $<

test: $(TARGET)
	@echo "=== memdb unit tests ==="
	$(MAKE) $(TEST_LIBMEMDB)
	./$(TEST_LIBMEMDB)
	@echo ""
	@echo "=== memdb RPC integration tests (requires running server) ==="
	-python3 test_memdb.py $(HOST) $(PORT)

test-libmemdb: $(TEST_LIBMEMDB)
	./$(TEST_LIBMEMDB)

test-memdb: $(TARGET)
	@echo "Starting memdb server..."
	@./$(TARGET) --host $(HOST) --port $(PORT) --workers 2 --max-time 10 >/dev/null 2>&1 & echo $$! > .memdb.pid
	@sleep 1
	@python3 test_memdb.py $(HOST) $(PORT); EXIT=$$?; \
	kill $$(cat .memdb.pid) 2>/dev/null; rm -f .memdb.pid; exit $$EXIT

clean:
	rm -f $(TARGET) $(TEST_LIBMEMDB) .memdb.pid

.PHONY: all clean test test-libmemdb test-memdb
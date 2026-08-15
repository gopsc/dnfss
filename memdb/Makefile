CPP      = g++
CPPFLAGS = -std=c++20 -O3 -Wall -Werror -pipe
INCS     = -I ../libmsg/inc -I ../libmemdb/inc
LIBS     = -L ../libmsg/build -lus_msg -lboost_json -lboost_program_options -lpthread
RPATH    = -Wl,-rpath,'$$ORIGIN/../../libmsg/build'

BUILD    = build
TARGET   = $(BUILD)/memdb
SRC      = src/memdb.cpp

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
	./$(TARGET) --host $(HOST) --port $(PORT) --workers 2 --max-time 10 &
	@sleep 1
	-python3 test_memdb.py $(HOST) $(PORT); EXIT=$$?
	@kill %1 2>/dev/null; wait %1 2>/dev/null
	@exit $$EXIT

clean:
	rm -f $(TARGET) $(TEST_LIBMEMDB)

.PHONY: all clean test test-libmemdb test-memdb
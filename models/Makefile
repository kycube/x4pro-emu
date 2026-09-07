CC ?= cc
CFLAGS ?= -O2 -g -Wall -Wextra -std=c11
BUILD := build

all: $(BUILD)/libepd.a $(BUILD)/test_epd $(BUILD)/test_windows $(BUILD)/replay

$(BUILD):
	mkdir -p $(BUILD)

$(BUILD)/epd_core.o: epd_core.c epd_core.h | $(BUILD)
	$(CC) $(CFLAGS) -c $< -o $@

$(BUILD)/libepd.a: $(BUILD)/epd_core.o
	ar rcs $@ $^

$(BUILD)/test_epd: tests/test_epd.c $(BUILD)/libepd.a
	$(CC) $(CFLAGS) -I. $< $(BUILD)/libepd.a -o $@

$(BUILD)/test_windows: tests/test_windows.c $(BUILD)/libepd.a
	$(CC) $(CFLAGS) -I. $< $(BUILD)/libepd.a -o $@

$(BUILD)/replay: tests/replay.c $(BUILD)/libepd.a
	$(CC) $(CFLAGS) -I. $< $(BUILD)/libepd.a -o $@

test: $(BUILD)/test_epd $(BUILD)/test_windows $(BUILD)/replay
	$(BUILD)/test_epd
	$(BUILD)/test_windows
	for f in fixtures/*.log; do [ -f "$$f" ] || continue; v=$$(basename $$f | sed -E 's/^([a-z0-9]+)-.*/\1/'); echo "replay $$v $$f"; $(BUILD)/replay $$v $$f || exit 1; done

clean:
	rm -rf $(BUILD)

.PHONY: all test clean

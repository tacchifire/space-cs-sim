# Host build of the firmware codec. No Zephyr, no board - the codec is deliberately free of both
# so it can be unit tested and later fuzzed on the host.
CFLAGS ?= -std=c11 -Wall -Wextra -Werror -O1 -g -I../../firmware/common
SRC := ../../firmware/common/cuberange_proto.c test_proto.c

.PHONY: test asan fuzz clean
ITERS ?= 200000
SEED  ?= 1

# `test` runs all three, because the two that were not run were the two the documents claimed.
# SECURITY.md said this codec "is fuzzed and sanitised": there was no fuzzer, and `make check`
# ran the plain build and never the asan one.
test: test_proto asan fuzz
	./test_proto

# The codec parses attacker-controlled bytes, so it gets sanitised as well as compiled.
asan: $(SRC) ../../firmware/common/cuberange_proto.h
	$(CC) $(CFLAGS) -fsanitize=address,undefined -o test_proto_asan $(SRC)
	./test_proto_asan

# A seeded random campaign under ASAN and UBSAN. NOT coverage-guided: libFuzzer is not available
# in this toolchain (`cc` is zig cc, which ships no fuzzer runtime) and afl, honggfuzz and radamsa
# are not installed - all four checked. Deterministic from its seed, so a finding is reproducible.
#
# 200000 iterations is about 1.3 seconds, which is why `check` can afford it every time. Give it
# ITERS and SEED for a longer campaign.
fuzz: fuzz_proto.c $(SRC) ../../firmware/common/cuberange_proto.h
	$(CC) $(CFLAGS) -fsanitize=address,undefined -o fuzz_proto \
	    ../../firmware/common/cuberange_proto.c fuzz_proto.c
	./fuzz_proto $(ITERS) $(SEED)

test_proto: $(SRC) ../../firmware/common/cuberange_proto.h
	$(CC) $(CFLAGS) -o $@ $(SRC)

clean:
	rm -f test_proto test_proto_asan fuzz_proto

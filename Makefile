.PHONY: all download export migrate verify query test clean

all:      ; python -m legomig all
download: ; python -m legomig download
export:   ; python -m legomig export
migrate:  ; python -m legomig migrate
verify:   ; python -m legomig verify
query:    ; python -m legomig query
test:     ; pytest -q
clean:    ; rm -rf out

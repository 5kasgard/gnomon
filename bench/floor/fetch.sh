#!/bin/sh
# Fetch the two public datasets (not redistributed here): the UCI SMS Spam
# Collection (Almeida and Hidalgo 2011, CC BY 4.0) and a 10,000-review sample
# of the Yelp Dataset Challenge, both as packaged in github.com/justmarkham/DAT8.
set -e
cd "$(dirname "$0")"
tmp=$(mktemp -d)
git clone -q --depth 1 --filter=blob:none --sparse https://github.com/justmarkham/DAT8 "$tmp/dat8"
git -C "$tmp/dat8" sparse-checkout set data
cp "$tmp/dat8/data/sms.tsv" "$tmp/dat8/data/yelp.csv" .
rm -rf "$tmp"

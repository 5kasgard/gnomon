//! SHA-256 (FIPS 180-4), standard library only.
//!
//! On x86-64 processors with the SHA extensions (Intel since Ice Lake and
//! Goldmont, AMD since Zen) the compression function runs on those
//! instructions, chosen once at run time; everywhere else, and as the
//! reference the hardware path is tested against, it is portable code.
//!
//! Correctness is checked against the FIPS test vectors in this module and,
//! independently, against Python's `hashlib` in the cross-language tests.

const K: [u32; 64] = [
    0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5,
    0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174,
    0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
    0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967,
    0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
    0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
    0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
    0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2,
];

const H0: [u32; 8] = [
    0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a, 0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19,
];

fn compress_portable(state: &mut [u32; 8], block: &[u8]) {
    debug_assert_eq!(block.len(), 64);
    let mut w = [0u32; 64];
    for (i, chunk) in block.chunks_exact(4).enumerate() {
        w[i] = u32::from_be_bytes([chunk[0], chunk[1], chunk[2], chunk[3]]);
    }
    for i in 16..64 {
        let s0 = w[i - 15].rotate_right(7) ^ w[i - 15].rotate_right(18) ^ (w[i - 15] >> 3);
        let s1 = w[i - 2].rotate_right(17) ^ w[i - 2].rotate_right(19) ^ (w[i - 2] >> 10);
        w[i] = w[i - 16]
            .wrapping_add(s0)
            .wrapping_add(w[i - 7])
            .wrapping_add(s1);
    }
    let [mut a, mut b, mut c, mut d, mut e, mut f, mut g, mut h] = *state;
    for i in 0..64 {
        let s1 = e.rotate_right(6) ^ e.rotate_right(11) ^ e.rotate_right(25);
        let ch = (e & f) ^ (!e & g);
        let t1 = h
            .wrapping_add(s1)
            .wrapping_add(ch)
            .wrapping_add(K[i])
            .wrapping_add(w[i]);
        let s0 = a.rotate_right(2) ^ a.rotate_right(13) ^ a.rotate_right(22);
        let maj = (a & b) ^ (a & c) ^ (b & c);
        let t2 = s0.wrapping_add(maj);
        h = g;
        g = f;
        f = e;
        e = d.wrapping_add(t1);
        d = c;
        c = b;
        b = a;
        a = t1.wrapping_add(t2);
    }
    for (s, v) in state.iter_mut().zip([a, b, c, d, e, f, g, h]) {
        *s = s.wrapping_add(v);
    }
}

/// Compress whole 64-byte blocks (`blocks.len()` is a multiple of 64).
fn compress_blocks(state: &mut [u32; 8], blocks: &[u8], hardware: bool) {
    #[cfg(target_arch = "x86_64")]
    if hardware {
        // SAFETY: `hardware` is only true when the CPU reports the SHA,
        // SSSE3 and SSE4.1 features (see `has_hardware`).
        unsafe { ni::compress_blocks(state, blocks) };
        return;
    }
    let _ = hardware;
    for block in blocks.chunks_exact(64) {
        compress_portable(state, block);
    }
}

/// Whether this CPU has the SHA-256 instructions (checked once).
pub fn has_hardware() -> bool {
    #[cfg(target_arch = "x86_64")]
    {
        use std::sync::atomic::{AtomicU8, Ordering};
        static HW: AtomicU8 = AtomicU8::new(0); // 0 unknown, 1 no, 2 yes
        match HW.load(Ordering::Relaxed) {
            1 => false,
            2 => true,
            _ => {
                let yes = std::is_x86_feature_detected!("sha")
                    && std::is_x86_feature_detected!("ssse3")
                    && std::is_x86_feature_detected!("sse4.1");
                HW.store(if yes { 2 } else { 1 }, Ordering::Relaxed);
                yes
            }
        }
    }
    #[cfg(not(target_arch = "x86_64"))]
    false
}

/// SHA-256 digest of `data`.
pub fn digest(data: &[u8]) -> [u8; 32] {
    digest_with(data, has_hardware())
}

fn digest_with(data: &[u8], hardware: bool) -> [u8; 32] {
    let mut state = H0;
    let whole = data.len() - data.len() % 64;
    compress_blocks(&mut state, &data[..whole], hardware);
    let rem = &data[whole..];
    let bit_len = (data.len() as u64).wrapping_mul(8);
    let mut tail = [0u8; 128];
    tail[..rem.len()].copy_from_slice(rem);
    tail[rem.len()] = 0x80;
    let tail_len = if rem.len() < 56 { 64 } else { 128 };
    tail[tail_len - 8..tail_len].copy_from_slice(&bit_len.to_be_bytes());
    compress_blocks(&mut state, &tail[..tail_len], hardware);
    let mut out = [0u8; 32];
    for (i, word) in state.iter().enumerate() {
        out[i * 4..i * 4 + 4].copy_from_slice(&word.to_be_bytes());
    }
    out
}

/// SHA-256 over data given in pieces.
pub struct Hasher {
    state: [u32; 8],
    buf: [u8; 64],
    used: usize,
    len: u64,
    hardware: bool,
}

impl Default for Hasher {
    fn default() -> Self {
        Self::new()
    }
}

impl Hasher {
    pub fn new() -> Self {
        Hasher { state: H0, buf: [0; 64], used: 0, len: 0, hardware: has_hardware() }
    }

    pub fn update(&mut self, mut data: &[u8]) {
        self.len = self.len.wrapping_add(data.len() as u64);
        if self.used > 0 {
            let take = (64 - self.used).min(data.len());
            self.buf[self.used..self.used + take].copy_from_slice(&data[..take]);
            self.used += take;
            data = &data[take..];
            if self.used < 64 {
                return;
            }
            let block = self.buf;
            compress_blocks(&mut self.state, &block, self.hardware);
            self.used = 0;
        }
        let whole = data.len() - data.len() % 64;
        compress_blocks(&mut self.state, &data[..whole], self.hardware);
        let rest = &data[whole..];
        self.buf[..rest.len()].copy_from_slice(rest);
        self.used = rest.len();
    }

    pub fn finish(mut self) -> [u8; 32] {
        let bit_len = self.len.wrapping_mul(8);
        let mut tail = [0u8; 128];
        tail[..self.used].copy_from_slice(&self.buf[..self.used]);
        tail[self.used] = 0x80;
        let tail_len = if self.used < 56 { 64 } else { 128 };
        tail[tail_len - 8..tail_len].copy_from_slice(&bit_len.to_be_bytes());
        compress_blocks(&mut self.state, &tail[..tail_len], self.hardware);
        let mut out = [0u8; 32];
        for (i, word) in self.state.iter().enumerate() {
            out[i * 4..i * 4 + 4].copy_from_slice(&word.to_be_bytes());
        }
        out
    }
}

/// HMAC-SHA-256 (RFC 2104) of the concatenation of `parts`, under `key`.
pub fn hmac(key: &[u8], parts: &[&[u8]]) -> [u8; 32] {
    let mut k = [0u8; 64];
    if key.len() > 64 {
        k[..32].copy_from_slice(&digest(key));
    } else {
        k[..key.len()].copy_from_slice(key);
    }
    let mut inner = Hasher::new();
    inner.update(&k.map(|b| b ^ 0x36));
    for p in parts {
        inner.update(p);
    }
    let mut outer = Hasher::new();
    outer.update(&k.map(|b| b ^ 0x5c));
    outer.update(&inner.finish());
    outer.finish()
}

/// Lowercase hex SHA-256 of `data`.
pub fn hex_digest(data: &[u8]) -> String {
    const HEX: &[u8; 16] = b"0123456789abcdef";
    let d = digest(data);
    let mut s = String::with_capacity(64);
    for byte in d {
        s.push(HEX[(byte >> 4) as usize] as char);
        s.push(HEX[(byte & 0x0f) as usize] as char);
    }
    s
}

#[cfg(target_arch = "x86_64")]
mod ni {
    //! The compression function on the x86 SHA extensions: two rounds per
    //! `sha256rnds2`, the message schedule on `sha256msg1`/`sha256msg2`.
    use super::K;
    use std::arch::x86_64::*;

    #[inline(always)]
    unsafe fn schedule(v0: __m128i, v1: __m128i, v2: __m128i, v3: __m128i) -> __m128i {
        let t1 = _mm_sha256msg1_epu32(v0, v1);
        let t2 = _mm_alignr_epi8(v3, v2, 4);
        _mm_sha256msg2_epu32(_mm_add_epi32(t1, t2), v3)
    }

    #[inline(always)]
    unsafe fn rounds4(abef: &mut __m128i, cdgh: &mut __m128i, w: __m128i, i: usize) {
        let k = _mm_loadu_si128(K.as_ptr().add(4 * i) as *const __m128i);
        let t1 = _mm_add_epi32(w, k);
        *cdgh = _mm_sha256rnds2_epu32(*cdgh, *abef, t1);
        let t2 = _mm_shuffle_epi32(t1, 0x0E);
        *abef = _mm_sha256rnds2_epu32(*abef, *cdgh, t2);
    }

    /// # Safety
    /// The CPU must support the `sha`, `ssse3` and `sse4.1` features.
    #[target_feature(enable = "sha,sse2,ssse3,sse4.1")]
    pub unsafe fn compress_blocks(state: &mut [u32; 8], blocks: &[u8]) {
        let mask = _mm_set_epi64x(0x0C0D_0E0F_0809_0A0Bu64 as i64, 0x0405_0607_0001_0203u64 as i64);
        let p = state.as_ptr() as *const __m128i;
        let dcba = _mm_loadu_si128(p);
        let efgh = _mm_loadu_si128(p.add(1));
        let cdab = _mm_shuffle_epi32(dcba, 0xB1);
        let efgh = _mm_shuffle_epi32(efgh, 0x1B);
        let mut abef = _mm_alignr_epi8(cdab, efgh, 8);
        let mut cdgh = _mm_blend_epi16(efgh, cdab, 0xF0);
        for block in blocks.chunks_exact(64) {
            let (abef0, cdgh0) = (abef, cdgh);
            let d = block.as_ptr() as *const __m128i;
            let mut w = [
                _mm_shuffle_epi8(_mm_loadu_si128(d), mask),
                _mm_shuffle_epi8(_mm_loadu_si128(d.add(1)), mask),
                _mm_shuffle_epi8(_mm_loadu_si128(d.add(2)), mask),
                _mm_shuffle_epi8(_mm_loadu_si128(d.add(3)), mask),
            ];
            for (i, wi) in w.iter().enumerate() {
                rounds4(&mut abef, &mut cdgh, *wi, i);
            }
            for i in 4..16 {
                // w[i % 4] holds W[i-4 .. i-1] (four words): the next four
                // come from the previous sixteen.
                let next = schedule(w[i % 4], w[(i + 1) % 4], w[(i + 2) % 4], w[(i + 3) % 4]);
                w[i % 4] = next;
                rounds4(&mut abef, &mut cdgh, next, i);
            }
            abef = _mm_add_epi32(abef, abef0);
            cdgh = _mm_add_epi32(cdgh, cdgh0);
        }
        let feba = _mm_shuffle_epi32(abef, 0x1B);
        let dchg = _mm_shuffle_epi32(cdgh, 0xB1);
        let dcba = _mm_blend_epi16(feba, dchg, 0xF0);
        let hgef = _mm_alignr_epi8(dchg, feba, 8);
        let q = state.as_mut_ptr() as *mut __m128i;
        _mm_storeu_si128(q, dcba);
        _mm_storeu_si128(q.add(1), hgef);
    }
}

#[cfg(test)]
mod tests {
    use super::{digest_with, has_hardware, hex_digest};

    #[test]
    fn pieces_hash_as_the_whole() {
        let data: Vec<u8> = (0..1000u32).map(|i| (i * 7 + 3) as u8).collect();
        for cut in [0, 1, 55, 63, 64, 65, 127, 128, 500, 999, 1000] {
            for cut2 in [cut, (cut + 70).min(1000)] {
                let mut h = super::Hasher::new();
                h.update(&data[..cut]);
                h.update(&data[cut..cut2]);
                h.update(&data[cut2..]);
                assert_eq!(h.finish(), super::digest(&data), "{cut} {cut2}");
            }
        }
    }

    #[test]
    fn hmac_rfc4231() {
        // RFC 4231 test cases 1, 2 and 6 (key longer than a block).
        let hex = |d: [u8; 32]| d.iter().map(|b| format!("{b:02x}")).collect::<String>();
        assert_eq!(
            hex(super::hmac(&[0x0b; 20], &[b"Hi ", b"There"])),
            "b0344c61d8db38535ca8afceaf0bf12b881dc200c9833da726e9376c2e32cff7"
        );
        assert_eq!(
            hex(super::hmac(b"Jefe", &[b"what do ya want for nothing?"])),
            "5bdcc146bf60754e6a042426089575c75a003f089d2739839dec58b964ec3843"
        );
        assert_eq!(
            hex(super::hmac(&[0xaa; 131], &[b"Test Using Larger Than Block-Size Key - Hash Key First"])),
            "60e431591ee0b67f0d8a26aacbf5b77f8e0bc6213728c5140546040f0ee37f54"
        );
    }

    #[test]
    fn hardware_matches_portable() {
        // Every length across several blocks and the padding boundaries,
        // with varied bytes: the two paths must agree exactly.
        if !has_hardware() {
            return;
        }
        let mut x: u32 = 12345;
        let data: Vec<u8> = (0..1100)
            .map(|_| {
                x = x.wrapping_mul(1_103_515_245).wrapping_add(12_345);
                (x >> 16) as u8
            })
            .collect();
        for n in 0..data.len() {
            assert_eq!(digest_with(&data[..n], true), digest_with(&data[..n], false), "length {n}");
        }
    }

    // FIPS 180-4 / NIST CAVS examples.
    #[test]
    fn empty() {
        assert_eq!(
            hex_digest(b""),
            "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
        );
    }

    #[test]
    fn abc() {
        assert_eq!(
            hex_digest(b"abc"),
            "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
        );
    }

    #[test]
    fn two_block_message() {
        assert_eq!(
            hex_digest(b"abcdbcdecdefdefgefghfghighijhijkijkljklmklmnlmnomnopnopq"),
            "248d6a61d20638b8e5c026930c3e6039a33ce45964ff2167f6ecedd419db06c1"
        );
    }

    #[test]
    fn million_a() {
        let data = vec![b'a'; 1_000_000];
        assert_eq!(
            hex_digest(&data),
            "cdc76e5c9914fb9281a1c7e284d73e67f1809a48a497200e046d39ccc7112cd0"
        );
    }

    #[test]
    fn padding_boundaries() {
        // Lengths around the 55/56/64 byte padding boundaries must not panic
        // and must all differ.
        let mut seen = std::collections::HashSet::new();
        for n in 50..130 {
            assert!(seen.insert(hex_digest(&vec![0x61; n])));
        }
    }
}

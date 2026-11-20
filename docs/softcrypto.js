/* AES-256-GCM in plain JavaScript, for the phone page when the PC itself serves it over Wi-Fi.

   A browser only offers its own AES (crypto.subtle) to pages that came over https. In local mode
   the page comes from http://<your PC>.local, so the sealing is done here instead, with the same
   layout: 12 bytes of nonce, the ciphertext, 16 bytes of tag. tests/ checks it against the PC's.

   window.softGcm(key) -> { seal(nonce, plain) -> ciphertext + tag, open(nonce, sealed) -> plain }
   All of them are Uint8Array. open() throws when the tag does not fit. */
(function () {
  "use strict";

  var SBOX = new Uint8Array(256);
  var T0 = new Uint32Array(256), T1 = new Uint32Array(256), T2 = new Uint32Array(256), T3 = new Uint32Array(256);

  (function tables() {
    var p = 1, q = 1;
    function left(value, by) { return ((value << by) | (value >>> (8 - by))) & 0xff; }
    do {
      p = (p ^ (p << 1) ^ (p & 0x80 ? 0x1b : 0)) & 0xff;   // p times 3
      q = (q ^ (q << 1)) & 0xff;                           // q divided by 3
      q = (q ^ (q << 2)) & 0xff;
      q = (q ^ (q << 4)) & 0xff;
      if (q & 0x80) q ^= 0x09;
      SBOX[p] = q ^ left(q, 1) ^ left(q, 2) ^ left(q, 3) ^ left(q, 4) ^ 0x63;
    } while (p !== 1);
    SBOX[0] = 0x63;
    for (var x = 0; x < 256; x += 1) {
      var s = SBOX[x], s2 = ((s << 1) ^ (s & 0x80 ? 0x1b : 0)) & 0xff, s3 = s2 ^ s;
      var word = ((s2 << 24) | (s << 16) | (s << 8) | s3) >>> 0;
      T0[x] = word;
      T1[x] = ((word >>> 8) | (word << 24)) >>> 0;
      T2[x] = ((word >>> 16) | (word << 16)) >>> 0;
      T3[x] = ((word >>> 24) | (word << 8)) >>> 0;
    }
  }());

  function substitute(word) {
    return ((SBOX[word >>> 24] << 24) | (SBOX[(word >>> 16) & 255] << 16) | (SBOX[(word >>> 8) & 255] << 8) | SBOX[word & 255]) >>> 0;
  }

  /* The 60 round-key words of a 256-bit key. */
  function expand(key) {
    var words = new Uint32Array(60), round = 1;
    for (var i = 0; i < 8; i += 1) {
      words[i] = ((key[4 * i] << 24) | (key[4 * i + 1] << 16) | (key[4 * i + 2] << 8) | key[4 * i + 3]) >>> 0;
    }
    for (i = 8; i < 60; i += 1) {
      var last = words[i - 1];
      if (i % 8 === 0) {
        last = (substitute(((last << 8) | (last >>> 24)) >>> 0) ^ (round << 24)) >>> 0;
        round = ((round << 1) ^ (round & 0x80 ? 0x1b : 0)) & 0xff;
      } else if (i % 8 === 4) {
        last = substitute(last);
      }
      words[i] = (words[i - 8] ^ last) >>> 0;
    }
    return words;
  }

  /* One block through AES: four big-endian words in `block`, changed in place. */
  function encrypt(keys, block) {
    var s0 = block[0] ^ keys[0], s1 = block[1] ^ keys[1], s2 = block[2] ^ keys[2], s3 = block[3] ^ keys[3];
    var t0, t1, t2, t3, at = 4;
    for (var round = 1; round < 14; round += 1) {
      t0 = T0[s0 >>> 24] ^ T1[(s1 >>> 16) & 255] ^ T2[(s2 >>> 8) & 255] ^ T3[s3 & 255] ^ keys[at];
      t1 = T0[s1 >>> 24] ^ T1[(s2 >>> 16) & 255] ^ T2[(s3 >>> 8) & 255] ^ T3[s0 & 255] ^ keys[at + 1];
      t2 = T0[s2 >>> 24] ^ T1[(s3 >>> 16) & 255] ^ T2[(s0 >>> 8) & 255] ^ T3[s1 & 255] ^ keys[at + 2];
      t3 = T0[s3 >>> 24] ^ T1[(s0 >>> 16) & 255] ^ T2[(s1 >>> 8) & 255] ^ T3[s2 & 255] ^ keys[at + 3];
      s0 = t0; s1 = t1; s2 = t2; s3 = t3;
      at += 4;
    }
    block[0] = (((SBOX[s0 >>> 24] << 24) | (SBOX[(s1 >>> 16) & 255] << 16) | (SBOX[(s2 >>> 8) & 255] << 8) | SBOX[s3 & 255]) ^ keys[56]) >>> 0;
    block[1] = (((SBOX[s1 >>> 24] << 24) | (SBOX[(s2 >>> 16) & 255] << 16) | (SBOX[(s3 >>> 8) & 255] << 8) | SBOX[s0 & 255]) ^ keys[57]) >>> 0;
    block[2] = (((SBOX[s2 >>> 24] << 24) | (SBOX[(s3 >>> 16) & 255] << 16) | (SBOX[(s0 >>> 8) & 255] << 8) | SBOX[s1 & 255]) ^ keys[58]) >>> 0;
    block[3] = (((SBOX[s3 >>> 24] << 24) | (SBOX[(s0 >>> 16) & 255] << 16) | (SBOX[(s1 >>> 8) & 255] << 8) | SBOX[s2 & 255]) ^ keys[59]) >>> 0;
  }

  /* y = y times h in GCM's field, both as four big-endian words. */
  function multiply(y, h) {
    var z0 = 0, z1 = 0, z2 = 0, z3 = 0, v0 = h[0], v1 = h[1], v2 = h[2], v3 = h[3];
    for (var word = 0; word < 4; word += 1) {
      var bits = y[word];
      for (var bit = 31; bit >= 0; bit -= 1) {
        if ((bits >>> bit) & 1) { z0 ^= v0; z1 ^= v1; z2 ^= v2; z3 ^= v3; }
        var carry = v3 & 1;
        v3 = (v3 >>> 1) | (v2 << 31);
        v2 = (v2 >>> 1) | (v1 << 31);
        v1 = (v1 >>> 1) | (v0 << 31);
        v0 = v0 >>> 1;
        if (carry) v0 ^= 0xe1000000;
      }
    }
    y[0] = z0 >>> 0; y[1] = z1 >>> 0; y[2] = z2 >>> 0; y[3] = z3 >>> 0;
  }

  function wordAt(bytes, at) {
    var end = bytes.length;
    return (((at < end ? bytes[at] : 0) << 24) | ((at + 1 < end ? bytes[at + 1] : 0) << 16) |
      ((at + 2 < end ? bytes[at + 2] : 0) << 8) | (at + 3 < end ? bytes[at + 3] : 0)) >>> 0;
  }

  window.softGcm = function (key) {
    if (!key || key.length !== 32) throw new Error("the key must be 32 bytes");
    var keys = expand(key);
    var h = new Uint32Array(4);
    encrypt(keys, h);

    /* XORs `data` with the key stream that starts at counter 2 for this nonce. */
    function stream(nonce, data) {
      var out = new Uint8Array(data.length), block = new Uint32Array(4);
      var n0 = wordAt(nonce, 0), n1 = wordAt(nonce, 4), n2 = wordAt(nonce, 8), counter = 2;
      for (var at = 0; at < data.length; at += 16) {
        block[0] = n0; block[1] = n1; block[2] = n2; block[3] = counter;
        counter = (counter + 1) >>> 0;
        encrypt(keys, block);
        for (var i = 0; i < 16 && at + i < data.length; i += 1) {
          out[at + i] = data[at + i] ^ ((block[i >> 2] >>> (24 - 8 * (i & 3))) & 255);
        }
      }
      return out;
    }

    /* The 16-byte tag over the ciphertext (no extra data is authenticated). */
    function tag(nonce, cipher) {
      var y = new Uint32Array(4);
      for (var at = 0; at < cipher.length; at += 16) {
        y[0] ^= wordAt(cipher, at); y[1] ^= wordAt(cipher, at + 4); y[2] ^= wordAt(cipher, at + 8); y[3] ^= wordAt(cipher, at + 12);
        multiply(y, h);
      }
      y[2] ^= Math.floor(cipher.length / 0x20000000) >>> 0;   // the length in bits, as 64 bits
      y[3] ^= (cipher.length * 8) >>> 0;
      multiply(y, h);
      var first = new Uint32Array([wordAt(nonce, 0), wordAt(nonce, 4), wordAt(nonce, 8), 1]);
      encrypt(keys, first);
      var made = new Uint8Array(16);
      for (var i = 0; i < 16; i += 1) made[i] = ((y[i >> 2] ^ first[i >> 2]) >>> (24 - 8 * (i & 3))) & 255;
      return made;
    }

    return {
      seal: function (nonce, plain) {
        if (nonce.length !== 12) throw new Error("the nonce must be 12 bytes");
        var cipher = stream(nonce, plain), all = new Uint8Array(cipher.length + 16);
        all.set(cipher, 0);
        all.set(tag(nonce, cipher), cipher.length);
        return all;
      },
      open: function (nonce, sealed) {
        if (nonce.length !== 12 || sealed.length < 16) throw new Error("not sealed");
        var cipher = sealed.subarray(0, sealed.length - 16), given = sealed.subarray(sealed.length - 16);
        var made = tag(nonce, cipher), different = 0;
        for (var i = 0; i < 16; i += 1) different |= made[i] ^ given[i];
        if (different) throw new Error("the tag does not fit");
        return stream(nonce, cipher);
      }
    };
  };
}());

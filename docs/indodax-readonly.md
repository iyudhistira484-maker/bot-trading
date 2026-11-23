# Diagnostik akun Indodax TAPI v2 — baca saja

Ini pemeriksaan mandiri untuk **akun nyata**: membaca informasi akun dan jumlah order terbuka. **Bukan bot live**, tidak membuat/membatalkan order, tidak memindahkan aset, tidak menyimpan respons ke database, dan tidak digunakan untuk rekonsiliasi otomatis. Jalankan hanya setelah Anda memahami risikonya dan secara khusus setuju memeriksa akun nyata. Pengujian kode saat ini memakai respons palsu; koneksi akun nyata belum diverifikasi.

## Dokumentasi dan perbedaan versi

Dokumentasi resmi yang diperiksa: [TAPI v2 private](https://github.com/btcid/indodax-official-api-docs/blob/master/INDODAX-TradeAPI-2-Private.md), repo pada commit `8d1b4097933ba793af841d849bf7d9c51f009e9d` (2026-11-10); cek lagi sebelum memakai karena bisa berubah. Dokumen menyatakan TAPI v2 memerlukan **API key v2 khusus**, terpisah dari key TAPI lama, dan memakai HMAC-SHA256. `GET /api/v2/account` menyatakan informasi izin dan saldo; `GET /api/v2/openOrders` menyatakan order terbuka termasuk yang terisi sebagian. Tidak ada asumsi bahwa snapshot ini lengkap atau final untuk rekonsiliasi.

Dokumen lama [TAPI private](https://github.com/btcid/indodax-official-api-docs/blob/master/Private-RestAPI.md#order-history) menyatakan order history lama dihentikan 7 April 2026; tautan lama `INDODAX-TradeAPI-2.md` saat ini tidak ditemukan, dan digantikan tautan v2 private di atas. Adapter ccxt 4.5.93 yang diperiksa masih menggunakan endpoint history lama. Diagnostik v2 ini memakai `requests` langsung untuk dua endpoint GET itu; **bukan implementasi live ccxt**, dan tidak menyelesaikan masalah order history, partial fill, timeout order, atau pemulihan.

## Keamanan

- Buat key v2 **Reading/View Only**, dengan trade **dan** semua izin withdraw nonaktif. Jangan gunakan key berizin trade untuk pemeriksaan ini. Periksa izin di situs/aplikasi resmi Indodax. Respons account harus memuat `canTrade`, `canWithdraw`, dan `canWithdrawFiat` yang semuanya `false`; jika tidak ada atau tidak demikian, diagnostik berhenti sebelum meminta open orders. Ini bukan pengganti pemeriksaan izin di akun Anda.
- Dokumen v2 saat diperiksa menyatakan IP permission hanya tersedia/diperlukan saat izin withdrawal dipilih. **Jangan aktifkan withdrawal untuk mendapat IP restriction**. Periksa kembali opsi terkini di Indodax.
- Masukkan key dan secret **hanya di prompt terminal interaktif**; input tidak ditampilkan, tidak diterima lewat argumen, pipe, atau variabel lingkungan oleh CLI ini, dan tidak ditulis ke file/log oleh kode. Jangan tempel kredensial atau data akun di chat, shell command, Git, screenshot, atau jurnal. Proses yang memiliki akses pada perangkat/akun VPS masih bisa membahayakan kredensial; ini bukan penyimpanan rahasia yang kebal kompromi.
- Koneksi HTTPS ke host tetap, hanya GET untuk dua endpoint di atas; TLS aktif, redirect dan proxy dari environment tidak digunakan. Respons dan exception mentah tidak dicetak. Tidak ada retry otomatis. Jika jam VPS tidak akurat, timeout, izin salah, atau respons tidak sesuai dokumentasi, pemeriksaan gagal tertutup.
- Jalankan pada VPS/perangkat dan jaringan tepercaya, perbarui sistem, dan cabut key jika dicurigai bocor. Pembatasan tool tidak melindungi dari penyalahgunaan key di luar tool ini; izin di Indodax adalah pengaman utama.

## Jalankan setelah tes lulus dan persetujuan

Dari folder checkout yang diuji, jalankan sebagai pengguna non-root di terminal interaktif:

```bash
.venv/bin/python -m src.indodax_readonly
```

Ketik `READ ONLY` lalu masukkan key dan secret v2 secara tersembunyi. Secara default hanya ditampilkan apakah saldo BTC/IDR tersedia, bukan nilainya, serta jumlah order terbuka yang dikembalikan untuk seluruh akun. Aset yang tidak dikembalikan ditandai tidak tersedia, **bukan dianggap nol**. Jangan bagikan output yang bersifat pribadi. Jika ingin melihat angka free/locked secara lokal, dapat memakai `--show-balances`; jangan kirim output tersebut ke chat. Diagnostik tidak memodifikasi saldo atau posisi.

Untuk tes tanpa koneksi atau kredensial asli:

```bash
.venv/bin/python -m pytest -q tests/test_indodax_readonly.py
```

Setelah itu, masih diperlukan uji akun nyata read-only dan verifikasi perilaku API; live tetap diblokir. Sebelum live dikembangkan, perlu desain eksekusi dan rekonsiliasi dengan order v2/ccxt yang kompatibel, recovery timeout dan partial fills, persistensi, pembatasan modal, pengujian keamanan serta izin eksplisit untuk setiap percobaan order. Tidak ada hasil profit yang dijamin.

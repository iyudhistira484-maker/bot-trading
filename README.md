# Indodax Spot Bot — tahap 4 (parsial)

Proyek masih dalam pembangunan. **Mode live sengaja diblokir dan tidak dapat mengirim order.** Bot paper lokal sudah memiliki loop pemantauan dan notifikasi opsional, tetapi hanya diuji dengan data palsu di sandbox; tidak ada pengujian end-to-end terhadap Indodax atau Telegram. Jangan gunakan untuk trading riil. Hasil bukan representasi risiko bot lengkap. Tidak ada jaminan profit.

## Setup dan test

Python 3.11+:

```bash
python -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
python -m pytest -q
```

Atur `config/config.yaml`. Rate fee, pajak dan slippage adalah **fraksi** (0.001 = 0.1%); nilai fee dan pajak yang disediakan hanyalah contoh, bukan informasi terkini. Periksa tarif, cakupan, serta cara perhitungan fee dan pajak terbaru di Indodax sebelum dipakai. Simulator saat ini mengasumsikan taker fee dan pajak proporsional atas nilai transaksi di **setiap sisi**, dan slippage proporsional pada kedua sisi; aturan aktual bisa berbeda. Maker fee disimpan untuk pengembangan berikutnya, tetapi tidak digunakan dalam simulasi.

## Backtest

Backtest satu pair per proses; setiap run memakai modal sendiri, bukan simulasi portofolio multipair. Sumber utama adalah OHLCV publik Indodax melalui ccxt. Ketersediaan dan jangkauan historis endpoint dapat terbatas; run gagal jika rentang yang diminta tidak lengkap atau ada celah. Jangan mengisi celah secara sintetis. Jika data kurang, impor CSV OHLCV dari sumber lain yang tepercaya untuk pair/timeframe yang sama, dengan memahami potensi perbedaan harga, likuiditas, dan zona waktu. Data sumber lain tidak otomatis kompatibel dengan Indodax.

```bash
python -m src.backtest.run --since 2024-01-01 --pair BTC/IDR --output reports/btc
# atau
python -m src.backtest.run --csv data/btc_1h.csv --pair BTC/IDR --output reports/btc
```

CSV harus memuat kolom `timestamp,open,high,low,close,volume`; timestamp UTC ISO 8601 atau epoch milidetik adalah waktu **open** candle. Data harus kontinu, unik, valid, dan terjajar timeframe. Runner menghapus candle yang belum tutup. Untuk timeframe selain 1h, periksa bahwa awal candle sumber sesuai kelipatan epoch UTC yang dipakai validator. Data, parameter yang dipakai, log trade, equity CSV, grafik, dan ringkasan disimpan pada folder output. Output dengan nama sama ditimpa.

Split default 70/30 berdasarkan waktu; tiap segmen mulai dengan modal sendiri dan tanpa posisi atau order bawaan. Indikator out-of-sample memakai candle sebelumnya sebagai warmup, tetapi parameter tidak otomatis dioptimasi. Pilih parameter hanya berdasarkan in-sample; jangan menyesuaikan berdasarkan hasil out-of-sample. Sinyal close dieksekusi di open candle berikutnya. Stop dan target diperiksa pada candle entry dan seterusnya; jika keduanya tercapai pada candle yang sama, stop dipilih secara konservatif. Gap dieksekusi di open, posisi tersisa ditutup di close terakhir; fill memakai biaya dan slippage. Durasi exit intrabar menggunakan waktu open candle karena urutan kejadian sebenarnya tidak diketahui. Batas rugi harian menghentikan entry hingga hari UTC berikutnya; baseline hari baru adalah equity pada close sebelumnya. Kill switch drawdown bersifat sticky, dinilai pada close, dan posisi yang tersisa dijual pada open berikutnya. Breach intrabar tidak dideteksi terpisah; posisi akhir segmen ditutup pada close terakhir. Tidak ada model likuiditas, spread, ukuran minimum, partial fill, atau keterlambatan; risiko aktual termasuk gap dapat melebihi anggaran 1%. Equity posisi terbuka ditandai pada close setelah estimasi biaya exit.

Laporan menyediakan total return, CAGR, max drawdown (negatif), Sharpe dan Sortino tahunan (risk-free 0), win rate, profit factor, jumlah trade, durasi rata-rata, dan expectancy dalam IDR. Return dan drawdown berupa fraksi. `N/A` berarti tidak terdefinisi; tidak ada rugi membuat profit factor N/A, bukan nol. Peringatan overfit adalah heuristik sederhana, bukan bukti statistik; panjang kedua segmen berbeda. Periode pendek, trade sedikit, dan kondisi pasar berbeda membatasi interpretasi hasil.

Mode `python -m src.main backtest --csv data/btc_1h.csv` menjalankan backtest yang sama. Tanpa argumen, `python -m src.main` memilih paper.

## Komponen risk dan paper (tahap 3)

Position sizing membatasi kerugian yang diperkirakan di stop, termasuk biaya dan slippage. Kerugian aktual dapat lebih besar akibat gap. Evaluasi menggunakan high-water mark untuk drawdown; batas rugi harian sticky selama hari UTC, dan kill tetap tersimpan saat restart. Jika bot offline saat pergantian hari, baseline harian paper adalah equity saat valuasi pertama hari itu, bukan rekonstruksi equity tepat tengah malam. Risk dievaluasi setiap siklus paper ketika data valid tersedia. Pada kill atau `/stop`, loop mencoba menjual seluruh posisi virtual dan terus mencoba bila ada yang gagal; bot berhenti hanya setelah posisi habis. Tidak ada stop di sisi exchange. Gangguan proses, jaringan, candle stale, quote tidak wajar, spread, atau minimum order bisa menghalangi penjualan. Walaupun alarm kill tersimpan, likuidasi tidak dijamin.

`PaperExecutor` menyimpan saldo IDR, posisi, order idempoten, dan risk state dalam SQLite; initial cash hanya diterapkan saat database dibuat pertama kali. SQLite menggunakan angka floating-point dan belum merupakan sistem akuntansi presisi produksi. Tidak boleh ada short atau averaging; sell harus menutup posisi penuh. Quote semua posisi harus tersedia, baru, dan punya bid/ask serta harga dan waktu close candle; spread dan deviasi dibatasi. Order membutuhkan metadata market spot aktif dan presisi/jumlah minimum dari ccxt. Jika batas minimum notional tidak disediakan metadata ccxt, order ditolak sampai `execution.min_notional_idr` dikonfigurasi per pair dengan angka yang diverifikasi. Adapter ccxt Indodax yang diperiksa memetakan `trade_min_base_currency` ke `price.min`; dokumentasi publik hanya menampilkan field tanpa menjelaskan semantiknya dengan cukup jelas. Kode tidak menebak bahwa angka tersebut adalah batas harga atau notional. Periksa [dokumentasi API Indodax](https://github.com/btcid/indodax-official-api-docs/blob/master/Public-RestAPI.md#pairs), kondisi transaksi terkini dan perilaku ccxt sebelum menetapkan nilai. Limit validasi ini bisa menghalangi penjualan kecil/dust.

## Menjalankan paper dan kontrol

Paper menggunakan API **publik** untuk candle dan ticker, serta saldo virtual lokal. Mode ini tidak memerlukan API key Indodax. Pastikan history tersedia dan tanpa celah, fee/pajak benar, dan minimal notional diverifikasi serta dikonfigurasi terlebih dahulu. Default `min_notional_idr: {}` sengaja dapat menyebabkan semua entry ditolak jika ccxt tidak menyediakan minimum notional. Kelola database SQLite dan backup dengan aman; saldo awal hanya digunakan saat database baru dibuat.

```bash
cp .env.example .env
# Opsional: isi TELEGRAM_BOT_TOKEN dan TELEGRAM_CHAT_ID pada .env
python -m src.main paper
```

Paper membuka posisi dari sinyal candle yang telah tutup hanya dalam `entry_window_seconds` sesudah open candle berikutnya. Eksekusi virtual memakai bid/ask terkini plus slippage, **bukan jaminan harga open tepat** seperti di backtest. Signal yang terlewat atau yang gagal validasi tidak diulang. Stop dan target dinilai melalui polling; harga dapat melampauinya antar-poll atau saat offline. Deviasi candle dari ticker membatasi entry, tetapi bukan exit, agar gap harga tidak dengan sendirinya menghalangi stop. Pemeriksaan kesegaran, spread, bid/ask/last dan limit tetap berlaku untuk exit dan bisa menyebabkan penolakan.

Telegram opsional: isi kedua variabel atau kosongkan keduanya. Gunakan chat privat yang hanya dapat Anda akses: otorisasi didasarkan pada chat ID, bukan identitas individu dalam grup. `/status` menampilkan status, `/pause` hanya menghentikan entry baru (exit tetap berjalan), `/resume` mengizinkan entry jika risk mengizinkan, `/stop` meminta likuidasi virtual lalu berhenti. `Ctrl+C` langsung menghentikan proses dan dapat meninggalkan posisi tidak terpantau. Heartbeat, ringkasan harian, entry, exit, error, dan kill dikirim best-effort; kegagalan Telegram tidak boleh dianggap bukti bahwa bot mati atau sehat. Log JSON berotasi di `logs/`; error hanya dicatat berdasarkan jenis, bukan pesan mentah yang mungkin berisi rahasia. Satu proses paper per database dibatasi oleh file lock lokal.

Kill dan stop tetap tersimpan saat restart. Tinjau state dan pastikan tidak ada posisi sebelum memakai `python -m src.main paper --reset-kill` atau `--reset-stop`; ketik `RESET PAPER` pada terminal interaktif. Reset kill mengatur ulang puncak equity tetapi tidak menghapus batas rugi harian. Opsi ini hanya untuk simulator, bukan mekanisme live.

## Live belum tersedia

`python -m src.main live` menolak eksekusi, bahkan jika `live_enabled: true` dan Anda mengonfirmasi `LIVE`. Tidak ada private API call maupun order live. Rekonsiliasi aman untuk timeout, partial fill, dan order yang belum terisi belum diimplementasikan. Dokumentasi [private API Indodax](https://github.com/btcid/indodax-official-api-docs/blob/master/Private-RestAPI.md#order-history) menyatakan endpoint order history dan trade history lama dihentikan pada 7 April 2026 dan menunjuk ke API v2; pada ccxt 4.5.93 yang diperiksa, `fetch_closed_orders` masih memanggil endpoint lama dan `fetchMyTrades` tidak tersedia. Ketersediaan aktual dan perilaku v2 perlu diverifikasi dengan dokumentasi resmi dan test terkontrol sebelum live dapat dibangun secara aman. `client_order_id` tercantum dalam dokumen, tetapi penanganan order yang hasilnya tidak diketahui tetap harus direkonsiliasi, bukan di-retry buta.

API key nantinya harus trade-only tanpa izin withdraw; gunakan IP whitelist jika tersedia. Jangan taruh kunci di kode atau log. `.env` diabaikan Git. Paper memakai `enableRateLimit`; retry dengan backoff hanya untuk public fetch. Test saat ini memakai market, quote, Telegram, dan order palsu; tidak ada koneksi nyata yang telah diverifikasi di sandbox.

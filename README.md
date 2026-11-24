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

Atur `config/config.yaml`. Rate fee, pajak dan slippage adalah **fraksi** (0.001 = 0.1%); fee dan pajak pada file konfigurasi adalah contoh untuk IDR Pro dari tabel yang diberikan pengguna pada 23 November 2026, bukan jaminan tarif terkini. Periksa tarif, cakupan, serta cara perhitungan fee dan pajak terbaru di Indodax sebelum dipakai. Simulator memakai fee dan pajak proporsional atas nilai transaksi, dengan pengaturan **beli dan jual terpisah**, serta slippage proporsional pada kedua sisi; aturan aktual bisa berbeda. Kolom `taker_buy_fee_rate`, `taker_sell_fee_rate`, `buy_tax_rate`, dan `sell_tax_rate` wajib disetel setelah verifikasi. Contoh ini memasukkan service fee dan CFX dalam kolom fee, serta pajak secara terpisah; jangan memasukkan all-in fee lalu menambahkan pajak yang sama lagi. Maker fee juga dapat dikonfigurasi terpisah tetapi tidak digunakan dalam simulasi taker. Struktur konfigurasi biaya baru ini menggantikan `maker_fee_rate`, `taker_fee_rate`, dan `tax_rate` lama; konfigurasi kustom lama perlu dimigrasikan secara manual, bukan ditimpa tanpa backup.

## Backtest

Backtest satu pair per proses; setiap run memakai modal sendiri, bukan simulasi portofolio multipair. Sumber utama adalah OHLCV publik Indodax melalui ccxt. Backtest membagi permintaan menjadi rentang terbatas untuk mengurangi pemotongan data lama; satuan `until` diuji terhadap adapter Indodax ccxt 4.5.93. Perilaku versi lain dan ketersediaan data tetap harus diverifikasi. Ketersediaan dan jangkauan historis endpoint dapat terbatas; run gagal jika rentang yang diminta tidak lengkap atau ada celah. Jangan mengisi celah secara sintetis. Jika data kurang, impor CSV OHLCV dari sumber lain yang tepercaya untuk pair/timeframe yang sama, dengan memahami potensi perbedaan harga, likuiditas, dan zona waktu. Data sumber lain tidak otomatis kompatibel dengan Indodax.

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

`PaperExecutor` menyimpan saldo IDR, posisi, order idempoten, dan risk state dalam SQLite; initial cash hanya diterapkan saat database dibuat pertama kali. SQLite menggunakan angka floating-point dan belum merupakan sistem akuntansi presisi produksi. Tidak boleh ada short atau averaging; sell harus menutup posisi penuh. Quote semua posisi harus tersedia, baru, dan punya bid/ask serta harga dan waktu close candle; spread dan deviasi dibatasi. Order membutuhkan metadata market spot aktif dan presisi/jumlah minimum dari ccxt. Jika batas minimum notional tidak disediakan metadata ccxt, order ditolak sampai `execution.min_notional_idr` dikonfigurasi per pair. Contoh BTC/IDR memakai Rp25.000 sebagai asumsi paper berdasarkan minimum order langsung Indodax Pro di [halaman bantuan Indodax](https://help.indodax.com/hc/en-us/articles/4416646599705-Details-of-Transaction-Fees-on-INDODAX); bukan bukti batas minimum API. Konfirmasi batas API sebelum menggunakan sistem nyata. Adapter ccxt Indodax yang diperiksa memetakan `trade_min_base_currency` ke `price.min`; dokumentasi publik hanya menampilkan field tanpa menjelaskan semantiknya dengan cukup jelas. Kode tidak menebak bahwa angka tersebut adalah batas harga atau notional. Periksa [dokumentasi API Indodax](https://github.com/btcid/indodax-official-api-docs/blob/master/Public-RestAPI.md#pairs), kondisi transaksi terkini dan perilaku ccxt sebelum menetapkan nilai. Limit validasi ini bisa menghalangi penjualan kecil/dust.

## Menjalankan paper dan kontrol

Paper menggunakan API **publik** untuk candle dan ticker, serta saldo virtual lokal. Mode ini tidak memerlukan API key Indodax. Pastikan history tersedia dan tanpa celah; verifikasi fee/pajak dan ketahui bahwa minimum notional contoh Rp25.000 adalah asumsi paper berdasarkan Indodax Pro, bukan minimum API yang telah diverifikasi. Hapus atau ubah asumsi tersebut jika tidak sesuai; tanpa minimum dari ccxt atau konfigurasi, entry akan ditolak. Kelola database SQLite dan backup dengan aman; saldo awal hanya digunakan saat database baru dibuat.

```bash
cp .env.example .env
# Opsional: isi TELEGRAM_BOT_TOKEN dan TELEGRAM_CHAT_ID pada .env
python -m src.main paper
```

Paper membuka posisi dari sinyal candle yang telah tutup hanya dalam `entry_window_seconds` sesudah open candle berikutnya. Eksekusi virtual memakai bid/ask terkini plus slippage, **bukan jaminan harga open tepat** seperti di backtest. Signal yang terlewat atau yang gagal validasi tidak diulang. Stop dan target dinilai melalui polling; harga dapat melampauinya antar-poll atau saat offline. Deviasi candle dari ticker membatasi entry, tetapi bukan exit, agar gap harga tidak dengan sendirinya menghalangi stop. Pemeriksaan kesegaran, spread, bid/ask/last dan limit tetap berlaku untuk exit dan bisa menyebabkan penolakan.

Telegram opsional: isi kedua variabel atau kosongkan keduanya. Gunakan chat privat yang hanya dapat Anda akses: otorisasi didasarkan pada chat ID, bukan identitas individu dalam grup. `/status` menampilkan status, `/pause` hanya menghentikan entry baru (exit tetap berjalan), `/resume` mengizinkan entry jika risk mengizinkan, `/stop` meminta likuidasi virtual lalu berhenti. `Ctrl+C` langsung menghentikan proses dan dapat meninggalkan posisi tidak terpantau. Heartbeat, ringkasan harian, entry, exit, error, dan kill dikirim best-effort; kegagalan Telegram tidak boleh dianggap bukti bahwa bot mati atau sehat. Log JSON berotasi di `logs/` dan dikirim ke journal untuk dilihat di Cockpit; error hanya dicatat berdasarkan jenis, bukan pesan mentah yang mungkin berisi rahasia. Satu proses paper per database dibatasi oleh file lock lokal.

Kill dan perintah Telegram `/stop` tetap tersimpan saat restart. Tinjau state dan pastikan tidak ada posisi sebelum memakai `python -m src.main paper --reset-kill --reset-only` atau `--reset-stop --reset-only` setelah service berhenti; ketik `RESET PAPER` pada terminal interaktif. Opsi `--reset-only` tidak menjalankan bot. Reset kill mengatur ulang puncak equity tetapi tidak menghapus batas rugi harian. Opsi ini hanya untuk simulator, bukan mekanisme live.

## Live belum tersedia

`python -m src.main live` menolak eksekusi, bahkan jika `live_enabled: true` dan Anda mengonfirmasi `LIVE`. Mode live tidak membuat private API call maupun order live. Diagnostik terpisah bersifat read-only dapat secara eksplisit memanggil API akun nyata; lihat di bawah. Rekonsiliasi aman untuk timeout, partial fill, dan order yang belum terisi belum diimplementasikan. Dokumentasi [private API Indodax](https://github.com/btcid/indodax-official-api-docs/blob/master/Private-RestAPI.md#order-history) menyatakan endpoint order history dan trade history lama dihentikan pada 7 April 2026 dan menunjuk ke [API v2](https://github.com/btcid/indodax-official-api-docs/blob/master/INDODAX-TradeAPI-2-Private.md); pada ccxt 4.5.93 yang diperiksa, `fetch_closed_orders` masih memanggil endpoint lama dan `fetchMyTrades` tidak tersedia. Ketersediaan aktual dan perilaku v2 perlu diverifikasi dengan dokumentasi resmi dan test terkontrol sebelum live dapat dibangun secara aman. `client_order_id` tercantum dalam dokumen, tetapi penanganan order yang hasilnya tidak diketahui tetap harus direkonsiliasi, bukan di-retry buta.

Diagnostik independen memakai key TAPI v2 khusus **view-only**, tanpa izin trade atau withdraw; lihat [panduan diagnostik read-only](docs/indodax-readonly.md). Ini belum diuji terhadap akun nyata dan tidak mengaktifkan live. Key untuk live nantinya memerlukan izin trade tanpa withdraw; gunakan IP whitelist jika tersedia, tanpa mengaktifkan withdraw hanya untuk whitelist. Jangan taruh kunci di kode atau log. `.env` diabaikan Git. Paper memakai `enableRateLimit`; retry dengan backoff hanya untuk public fetch. Test saat ini memakai market, quote, Telegram, dan order palsu; tidak ada koneksi nyata yang telah diverifikasi di sandbox.

## Penghentian service paper

Contoh unit systemd ada di `deploy/vps/indodax-paper.service`, tetapi tidak otomatis terpasang atau aktif. Berhenti lewat Cockpit/systemd mengirim SIGTERM: loop mencoba menyimpan permintaan stop, menghalangi entry baru, dan melikuidasi posisi virtual sebelum keluar. **Jika penutupan berhasil dan kill tidak aktif**, status stop sementara dihapus sehingga start berikutnya dapat melanjutkan; bila service di-enable, ini juga memungkinkan start setelah reboot normal. Penutupan dapat menimbulkan biaya virtual. Ini **best-effort**, bukan jaminan: timeout systemd, crash, SIGKILL, kegagalan data, validasi, dan koneksi dapat meninggalkan posisi virtual terbuka dan tidak terpantau. Jika shutdown belum selesai, status tetap tersimpan; start berikutnya mencoba menyelesaikan penutupan dan berhenti, sehingga mungkin memerlukan start manual berikutnya. Telegram `/stop` dan kill switch tetap tersimpan sampai reset manual; start/restart tidak menghapusnya. Unit memakai `Restart=on-failure` dan batas waktu stop 180 detik. Selalu cek status, log, dan posisi setelah berhenti atau reboot; selama downtime stop loss tidak dipantau.

### Dashboard paper lokal

Dashboard Home, posisi, riwayat, kontrol paper, dan pengaturan strategi/risiko tersedia melalui SSH tunnel saja. Lihat [panduan instalasi dan batasannya](deploy/vps/README.md#dashboard-paper-via-ssh-tunnel). Login dashboard memakai password terpisah; jangan buka port dashboard ke publik. Equity adalah snapshot terakhir, bukan data real-time. Dashboard tidak menyalakan service, tidak mendukung live, dan stop/exit adalah best-effort yang memerlukan pemeriksaan posisi dan log.

### Eksperimen offline: biaya dibanding jarak stop

Eksperimen terpisah `python -m src.research.cost_atr --report PATH_REPORT` membandingkan baseline terhadap satu hipotesis filter entry, hanya pada **segmen in-sample** report yang sudah ada. Ia membaca `candles.csv` dan `config_used.yaml` dari report, tanpa permintaan jaringan, perubahan file report, database, konfigurasi, atau bot. Pilih report/pair dengan benar; ini bukan fitur dashboard atau live. Pastikan hasil baseline sesuai dengan report asal sebelum menafsirkan perbandingan.

Pada sinyal candle yang sudah tutup, filter memperkirakan biaya satu putaran beli-jual pada harga close tersebut dengan fee/pajak taker dan slippage pada konfigurasi, tanpa spread. Rasio biaya per unit terhadap jarak stop `ATR × atr_stop_multiplier` harus **≤ 0,50**. Batas 0,50 adalah hipotesis tetap yang dipilih setelah hasil sebelumnya dilihat, bukan batas keamanan, nilai optimal, atau rekomendasi; estimasi bukan harga eksekusi aktual. Exit dan manajemen risiko tidak diubah. Jumlah sinyal ditampilkan sebelum simulasi; diblokirnya suatu sinyal tidak selalu berarti satu transaksi berkurang karena urutan dan posisi saling memengaruhi.

Eksperimen ini eksploratif dan **bukan uji buta**: hasil periode ini dan sebagian out-of-sample sudah pernah dilihat. Jangan memilih filter berdasarkan hasil satu periode atau menganggap output sebagai bukti keuntungan. Evaluasi lanjutan memerlukan rancangan yang dibekukan sebelum data baru, termasuk pengujian pada periode masa depan yang belum dipakai memilih parameter. Backtest tidak memodelkan spread aktual, likuiditas, minimum order atau partial fills dan tidak menjamin hasil riil. Bot paper dan live tidak menggunakan filter ini.

### Pengamatan shadow prospektif (tanpa order)

`src.research.shadow` mencatat **sinyal dan keputusan hipotetis filter**, bukan posisi, order, PnL, atau bukti profit. Ia tidak membaca/menulis database paper, tidak memuat `.env`, tidak memanggil endpoint privat, dan tidak mengubah layanan bot. Fetch candle publik terpisah dapat menghasilkan hasil berbeda dari paper karena keterlambatan/revisi data, jeda warmup rolling 500 candle, pause/posisi/risk, atau pengaturan dashboard paper. Study memakai **snapshot YAML** yang dipilih saat init, bukan override dashboard; periksa kesesuaian sebelum membandingkan.

Init membuat direktori study baru yang belum ada, dengan config dan SQLite sendiri; waktu mulai dan fingerprint kode/dependensi disimpan. Sample hanya mencatat candle yang **ditutup setelah init**, tidak backfill sinyal lama, menolak data kosong/gap/stale atau lebih dari 15 menit terlambat, dan tidak mencatat dua kali. Hitung candle terlewat pada observasi berikutnya. Status, waktu dan `journalctl` tetap harus dipantau: kegagalan/ketiadaan observasi, restart dan perubahan data bursa dapat mengurangi cakupan; candle terlewat yang belum diikuti observasi baru belum masuk hitungan. `Persistent=true` tidak mengisi kembali candle yang terlewat. Studi berhenti jika config, kode relevan atau versi dependensi berubah; evaluasi dan mulai study baru setelah review, jangan ubah metadata untuk menyamarkan perubahan. Code/hash bukan proteksi dari pengguna VPS yang sengaja memodifikasi file.

Contoh khusus layout VPS ini, dari clone percobaan (bukan clone bot aktif), setelah tes lulus:

```bash
cd /home/indodaxbot/dashboard-test
sudo -u indodaxbot /home/indodaxbot/indodax-bot/.venv/bin/python -m src.research.shadow init --dir /home/indodaxbot/shadow-cost-atr --config reports/btc-1tahun-20261123T054437Z/config_used.yaml
sudo systemd-analyze verify deploy/vps/indodax-shadow.service deploy/vps/indodax-shadow.timer
sudo cp deploy/vps/indodax-shadow.service deploy/vps/indodax-shadow.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now indodax-shadow.timer
systemctl list-timers indodax-shadow.timer --all
```

Status tanpa jaringan: `sudo -u indodaxbot /home/indodaxbot/indodax-bot/.venv/bin/python -m src.research.shadow status --dir /home/indodaxbot/shadow-cost-atr`. Log: `journalctl -u indodax-shadow.service -n 30 --no-pager`. Jika bermasalah, `sudo systemctl disable --now indodax-shadow.timer` menghentikan jadwal shadow saja, tanpa menyentuh paper. Service oneshot biasanya `inactive` setelah berhasil; cek timer dan log, bukan menganggap service `inactive` sebagai kegagalan. Direktori study mengandung data lokal dan tidak perlu diunggah ke Git.

Filter `≤ 0,50` dan biaya simulasi tetap hipotesis eksploratif yang dipilih setelah melihat data historis. Hasil observasi ke depan hanya mengukur keputusan sinyal, bukan kinerja trading. Jangan menyetel parameter berdasarkan hasil berjalan dan jangan aktifkan live karenanya; pengukuran hasil yang valid memerlukan rancangan evaluasi, asumsi eksekusi realistis, pemisahan data dan sampel memadai.

### Batas modal paper dan backtest

`risk.max_capital_idr` membatasi jumlah **biaya masuk posisi terbuka**, termasuk fee/pajak beli, di semua pair paper. Saldo tunai atau total equity boleh lebih besar. Batas dihitung dari biaya saat membeli, **bukan** harga pasar terkini: nilai posisi bisa melampaui batas setelah harga naik, dan batas ini tidak menjamin rugi maksimum. Modal dilepas kembali saat posisi ditutup. Sebelum membeli, paper mengecilkan ukuran agar sesuai budget dan memeriksa ulang secara transaksional; order masih bisa ditolak karena minimum/presisi, saldo, risiko, atau validasi harga. Jual/exit tidak diblokir oleh batas ini, meskipun masih tunduk pada pemeriksaan exit lainnya.

Jika posisi lama tidak mempunyai catatan biaya masuk yang dapat diverifikasi, pembelian baru diblokir sampai posisi ditutup atau data diperiksa; jangan membuat catatan atau mengubah database untuk melewati pemeriksaan. Perubahan batas menjadi lebih kecil dari posisi yang sudah terbuka tidak memaksa penjualan otomatis: pembelian baru diblokir sementara.

Backtest satu pair memakai batas yang sama untuk biaya pembelian setiap posisi, tetapi tidak memodelkan presisi, minimum exchange, spread, likuiditas atau partial fill. Laporan yang dibuat **sebelum** pembatasan modal diterapkan tidak otomatis berubah dan mungkin berbeda jika dijalankan ulang; tetap simpan sebagai hasil sebelumnya. Perubahan ini hanya untuk simulasi; live tetap diblokir.

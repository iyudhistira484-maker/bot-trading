# Panduan VPS Ubuntu: Cockpit dan folder bot

Panduan ini menyiapkan panel pengelolaan VPS dan folder khusus bot. **Tidak memasang atau memulai bot**, tidak membuka mode live, dan tidak memerlukan API key Indodax. Bot paper dan integrasi live masih memiliki keterbatasan yang dijelaskan pada README utama.

## Yang diperlukan

- VPS Ubuntu dan akses SSH dengan pengguna biasa yang punya izin `sudo` (hindari menjalankan bot sebagai root).
- Komputer untuk membuka terminal dan browser. Jika memakai ponsel, aplikasi SSH harus mendukung local port forwarding.
- Jangan kirim password SSH, private key, token Telegram, atau API key di chat.
- Pada firewall penyedia VPS, blokir akses masuk publik ke TCP 9090 sebelum memasang Cockpit. Jangan memblokir port SSH yang sedang digunakan. Petunjuk firewall berbeda menurut penyedia.

## 1. Masuk ke VPS dan periksa versi

Jalankan pada komputer lokal; ganti kedua nilai sesuai VPS:

```bash
ssh NAMA_PENGGUNA@IP_VPS
```

Di dalam VPS, jalankan:

```bash
cat /etc/os-release
python3 --version
```

Catat versi Ubuntu dan Python. Proyek membutuhkan Python **3.11 atau lebih baru**; jika versi bawaan lebih lama, jangan mengganti Python sistem Ubuntu. Minta panduan sesuai versi Ubuntu tersebut.

## 2. Buat folder khusus di VPS

Masih di terminal VPS, sebagai pengguna biasa:

```bash
mkdir -p "$HOME/indodax-bot"
cd "$HOME/indodax-bot"
pwd
```

Folder ini **masih kosong**. File proyek dalam workspace Arena tidak otomatis ada di VPS dan perubahan yang belum di-push tidak otomatis tersedia dari GitHub. File perlu dipindahkan kemudian, misalnya melalui SFTP atau setelah perubahan dipublikasikan secara eksplisit. Jangan memasukkan kredensial ke repository.

## 3. Pasang Cockpit dan alat Python

```bash
sudo apt update
sudo apt install -y cockpit python3 python3-venv git
```

Jika diminta password `sudo`, ketik di terminal VPS; jangan kirim ke chat. Paket Python pada langkah ini adalah versi bawaan Ubuntu dan belum tentu 3.11 atau lebih baru.

## 4. Batasi Cockpit hanya ke localhost VPS

Atur socket agar Cockpit hanya mendengarkan alamat loopback VPS; akses dari komputer sendiri dilakukan melalui SSH tunnel. Firewall penyedia tetap harus memblokir port 9090 dari internet.

```bash
sudo mkdir -p /etc/systemd/system/cockpit.socket.d
printf '[Socket]\nListenStream=\nListenStream=127.0.0.1:9090\n' | sudo tee /etc/systemd/system/cockpit.socket.d/local-only.conf >/dev/null
sudo systemctl daemon-reload
sudo systemctl enable --now cockpit.socket
sudo systemctl restart cockpit.socket
sudo systemctl status cockpit.socket --no-pager
sudo ss -ltn '( sport = :9090 )'
```

Periksa bahwa output `ss` hanya menunjukkan `127.0.0.1:9090`, bukan `0.0.0.0:9090` atau `[::]:9090`. Jika tidak sesuai, jangan lanjut membuka panel; periksa konfigurasi socket dan firewall. Jika port 9090 lokal sudah dipakai layanan lain, selesaikan konfliknya terlebih dahulu.

## 5. Buka panel melalui SSH tunnel

Di **komputer lokal**, buka terminal baru. Biarkan terminal ini tetap terbuka selama menggunakan panel:

```bash
ssh -N -L 9090:127.0.0.1:9090 NAMA_PENGGUNA@IP_VPS
```

Buka `https://localhost:9090` di browser pada **komputer yang menjalankan tunnel**, lalu masuk dengan akun Linux VPS. `localhost` di browser adalah komputer tersebut, bukan VPS secara langsung. Jika port lokal 9090 sudah dipakai, ganti menjadi `-L 9091:127.0.0.1:9090` dan buka `https://localhost:9091`.

Cockpit dapat menggunakan sertifikat self-signed. Jika browser memberi peringatan sertifikat, verifikasi fingerprint sertifikat VPS melalui kanal tepercaya sebelum melanjutkan; jangan abaikan peringatan yang tidak dipahami. SSH tunnel tidak menghilangkan kebutuhan untuk memverifikasi identitas VPS saat koneksi SSH pertama.

## 6. Langkah berikutnya

Setelah panel bisa dibuka dan versi Python memenuhi syarat, file bot harus dipindahkan ke VPS. Contoh unit untuk **paper** tersedia di `deploy/vps/indodax-paper.service`; periksa path dan lakukan pengujian sebelum memasangnya. Unit memakai `Restart=on-failure`, bukan restart otomatis tanpa syarat, agar keluar normal setelah kill switch tidak langsung menghidupkan ulang proses. Stop lewat Cockpit meminta loop melikuidasi posisi virtual; jika tuntas dan kill tidak aktif, status stop sementara dihapus agar start berikutnya bisa berjalan. Ini best-effort: timeout atau kegagalan data dapat meninggalkan posisi terbuka dan status stop tertunda. Telegram `/stop` dan kill tetap perlu pemeriksaan dan reset manual; start/restart tidak menghapusnya. Kill state tetap perlu ditinjau secara manual. Network outage atau proses berhenti dapat meninggalkan posisi paper tidak terpantau; Cockpit bukan jaminan bot selalu berjalan. Mode live tetap diblokir hingga integrasi aman selesai dan diuji.

## Dashboard paper via SSH tunnel

Dashboard ini **terpisah dari Cockpit**, hanya melayani paper dan tidak menyediakan live. Ia mengikat `127.0.0.1:8765` di VPS; **jangan buka port 8765 ke internet**. Password dashboard berbeda dari password Linux. Data melewati SSH tunnel, tetapi halaman lokal memakai HTTP: orang atau aplikasi lain yang dapat mengakses perangkat lokal, browser, atau akun VPS tetap merupakan risiko. Gunakan perangkat tepercaya dan tutup tunnel saat tidak digunakan.

Langkah ini disiapkan untuk instalasi setelah perubahan dashboard tersedia di branch dan tes di VPS lulus. Jangan menghentikan bot yang sedang berjalan tanpa memahami konsekuensi stop di atas. Menginstal dashboard sendiri tidak membutuhkan restart service paper.

1. Di terminal VPS, dari folder proyek dan setelah memperbarui branch kerja dengan aman, jalankan tes sebagai pengguna bot:

   ```bash
   cd /home/indodaxbot/indodax-bot
   sudo -u indodaxbot .venv/bin/python -m pytest -q
   ```

   Lanjutkan hanya jika semua tes lulus. Dashboard membaca database paper yang sudah ada; bila belum ada atau state tidak lengkap, ia akan menampilkan error alih-alih menciptakan saldo baru.

2. Buat password dashboard **secara interaktif** di terminal VPS. Pilih minimal 16 karakter unik dan jangan ketik password ke chat atau ke baris perintah. Perintah ini tidak menampilkan password dan tidak menimpa hash yang sudah ada:

   ```bash
   cd /home/indodaxbot/indodax-bot
   sudo -u indodaxbot .venv/bin/python -m src.dashboard setup
   ```

   Hash disimpan di `data/dashboard-auth.json` dengan izin 600; file ini tidak boleh diunggah ke GitHub. Untuk mengganti password, hentikan service dashboard, pindahkan atau hapus file hash lama **hanya setelah memastikan path benar**, jalankan setup lagi, lalu mulai dashboard kembali. Restart dashboard mengakhiri semua sesi.

3. Pasang dan periksa unit dashboard (ini tidak memulai ulang bot paper):

   ```bash
   sudo systemd-analyze verify /home/indodaxbot/indodax-bot/deploy/vps/indodax-dashboard.service
   sudo cp /home/indodaxbot/indodax-bot/deploy/vps/indodax-dashboard.service /etc/systemd/system/
   sudo systemctl daemon-reload
   sudo systemctl enable --now indodax-dashboard.service
   sudo systemctl status indodax-dashboard.service --no-pager
   sudo ss -ltn '( sport = :8765 )'
   ```

   Pastikan hasil `ss` menunjukkan **hanya** `127.0.0.1:8765`. Jika tidak, hentikan dashboard dan periksa konfigurasi. Cek log dengan `journalctl -u indodax-dashboard.service -n 50 --no-pager`. Jika port sudah dipakai, jangan membuka service alternatif ke publik.

4. Di perangkat lokal (bukan di shell VPS), buat local port forwarding SSH atau atur tunnel Termius: **local 8765 → remote 127.0.0.1:8765**. Contoh terminal:

   ```bash
   ssh -N -L 8765:127.0.0.1:8765 NAMA_PENGGUNA@IP_VPS
   ```

   Biarkan koneksi SSH aktif lalu buka `http://127.0.0.1:8765` pada perangkat yang menjalankan tunnel. Dashboard memvalidasi Host dan Origin sehingga gunakan alamat/port itu (atau `localhost:8765`); local port lain tidak didukung pada konfigurasi default. Jangan meneruskan local port ke seluruh jaringan atau membuka dashboard melalui reverse proxy publik. Jika memakai tombol Cockpit, tunnel Cockpit 9090 juga harus aktif di perangkat yang sama.

5. Masuk, bandingkan saldo/posisi/riwayat dengan data bot, dan cek timestamp equity; equity adalah snapshot terakhir, **bukan harga real-time**. Status “service aktif” tidak menjamin heartbeat atau harga masih segar. Riwayat grafik hanya memakai sebagian log saat ini; bukan kurva historis lengkap. Waktu tampilan mengikuti perangkat lokal.

**Kontrol:** Pause mencegah entry baru; exit dipantau hanya ketika bot dan data bekerja. Resume tidak menghapus stop atau kill. Stop dashboard meminta bot mencoba menjual posisi virtual lalu keluar; dapat gagal, jadi cek posisi dan log. Tombol reset stop hanya bekerja ketika bot berhenti, tidak ada posisi dan kill tidak aktif, dan tidak menyalakan bot. Start service dilakukan lewat Cockpit, bukan dari dashboard. Stop lewat Cockpit adalah permintaan shutdown service yang berbeda: setelah liquidasi tuntas, penanda shutdown sementara bisa dihapus; stop eksplisit dashboard tetap tertahan sampai direview dan direset. Jangan reset kill otomatis; ikuti prosedur manual setelah pemeriksaan.

**Pengaturan:** perubahan hanya dapat disimpan saat proses bot tidak memegang lock, tidak ada posisi terbuka, dan kill tidak aktif. Nilai divalidasi (risiko per trade maksimum 2%) dan dipakai pada startup paper berikutnya; backtest tetap memakai YAML. Menghentikan bot untuk mengubah pengaturan bisa memicu penjualan virtual dan biaya. Batas, fee, slippage, dan perilaku paper bukan bukti parameter atau hasil live. Tidak ada strategi yang menjamin keuntungan.

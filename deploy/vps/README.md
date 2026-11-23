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

Setelah panel bisa dibuka dan versi Python memenuhi syarat, file bot harus dipindahkan ke VPS. Contoh unit untuk **paper** tersedia di `deploy/vps/indodax-paper.service`; periksa path dan lakukan pengujian sebelum memasangnya. Unit memakai `Restart=on-failure`, bukan restart otomatis tanpa syarat, agar keluar normal setelah kill switch tidak langsung menghidupkan ulang proses. Stop lewat Cockpit meminta loop melikuidasi posisi virtual dan mencatat status stop, tetapi ini best-effort; timeout atau kegagalan data bisa meninggalkan posisi terbuka. Start/restart tidak menghapus status stop; pemeriksaan dan reset manual diperlukan. Kill state tetap perlu ditinjau secara manual. Network outage atau proses berhenti dapat meninggalkan posisi paper tidak terpantau; Cockpit bukan jaminan bot selalu berjalan. Mode live tetap diblokir hingga integrasi aman selesai dan diuji.

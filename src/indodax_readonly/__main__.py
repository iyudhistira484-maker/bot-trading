"""Interactive local diagnostic for a view-only Indodax TAPI v2 key."""
import argparse
import getpass
import logging
import sys
import warnings

from src.indodax_readonly.client import ReadOnlyClient, ReadOnlyError


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--show-balances', action='store_true', help='show actual IDR/BTC balance locally; do not share the output')
    args = parser.parse_args(argv)
    if not sys.stdin.isatty() or not sys.stderr.isatty():
        print('Terminal interaktif diperlukan; kredensial tidak boleh dimasukkan lewat argumen atau pipe.', file=sys.stderr)
        return 1
    print('Pemeriksaan akun nyata READ-ONLY. Tidak membuat, membatalkan, atau mengubah order.')
    print('Gunakan API key khusus TAPI v2 dengan izin VIEW ONLY, tanpa trade dan withdraw.')
    print('Data akun nyata bersifat pribadi; jangan bagikan kredensial atau output saldo.')
    try:
        if input('Ketik READ ONLY untuk melanjutkan: ').strip() != 'READ ONLY':
            print('Dibatalkan.'); return 1
        # getpass may otherwise fall back to echoing credentials on unsupported terminals.
        with warnings.catch_warnings():
            warnings.simplefilter('error', getpass.GetPassWarning)
            key = getpass.getpass('API key v2 (tidak ditampilkan): ')
            secret = getpass.getpass('API secret v2 (tidak ditampilkan): ')
        logging.disable(logging.CRITICAL)
        client = ReadOnlyClient(key, secret)
        del key, secret
        try:
            result = client.inspect()
        finally:
            client.close()
        print('Pemeriksaan selesai: respons akun dan order terbuka diterima.')
        print('Izin: view-only berdasarkan field respons API; verifikasi kembali di pengaturan Indodax.')
        print('Jumlah order terbuka di seluruh akun:', result.open_orders)
        for asset, values in result.balances.items():
            if values is None:
                print(asset + ': tidak disertakan dalam respons; bukan dianggap nol.')
            elif args.show_balances:
                print(asset + ': free=' + values[0] + ', locked=' + values[1])
            else:
                print(asset + ': data saldo tersedia (nilai disembunyikan).')
        print('Snapshot ini bukan rekonsiliasi live dan tidak mengaktifkan trading.')
        return 0
    except (ReadOnlyError, getpass.GetPassWarning) as exc:
        # ReadOnlyError messages are constants; warnings can contain terminal details.
        msg = str(exc) if isinstance(exc, ReadOnlyError) else 'Terminal tidak dapat menyembunyikan input.'
        print('Pemeriksaan gagal:', msg, file=sys.stderr)
        return 1
    except (EOFError, KeyboardInterrupt):
        print('\nDibatalkan.', file=sys.stderr)
        return 1
    except Exception:
        print('Pemeriksaan gagal; jangan bagikan kredensial atau respons mentah.', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())

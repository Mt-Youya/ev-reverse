use aes::Aes256;
use ecb::cipher::{block_padding::NoPadding, BlockEncryptMut, KeyInit};
use evmedia_core::{crypto::mask_from_filename, evs_manifest::Descriptor};

#[test]
fn descriptor_opens_and_manifest_round_trips() {
    let descriptor = Descriptor {
        host: "https://en2v4.ieway.cn".into(),
        req: "/student/getPlayTimeKeySignEVS20231103".into(),
        dkey: "x!@#y.cn_xnk0506".into(),
        dkey_ver: 202,
        cache_key: "evs_test".into(),
        base_key: "0123456789abcdef0123456789abcdef".into(),
    };
    let filename = "119354-00000000-0000-4000-8000-000000000000.evs";
    let text = "#EXTM3U\n#EXTINF:1.0,\n119354-00000000-0000-4000-8000-000000000001.ts\n#EXT-X-ENDLIST\n";
    let key = evmedia_core::crypto::key_from_text(&descriptor.base_key).unwrap();
    let mut plain = text.as_bytes().to_vec();
    let padding = (16 - plain.len() % 16) % 16;
    plain.extend(std::iter::repeat(b'#').take(padding));
    let plain_len = plain.len();
    let encrypted = ecb::Encryptor::<Aes256>::new_from_slice(&key).unwrap()
        .encrypt_padded_mut::<NoPadding>(&mut plain, plain_len)
        .unwrap()
        .to_vec();
    let mask = mask_from_filename(filename);
    let cipher: Vec<_> = encrypted.iter().enumerate().map(|(i, byte)| byte ^ mask[i % 16]).collect();
    assert_eq!(descriptor.manifest(&cipher, filename).unwrap(), text);
}

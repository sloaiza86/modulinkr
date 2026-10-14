"""Conflictos reales de identidad, destino, cifrado y persistencia local."""
import hashlib
import json
from pathlib import Path
import sqlite3
import struct
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import identity
import protocol
from buffer import GatewayBuffer

NODE='010203040501'
OTHER='010203040502'
TAG='1020304050607080'


def uplink(root,uid=NODE,boot=1,seq=7,ts=1000,value=21.0,key=None):
    payload=struct.pack('<IfB',ts,value,0)
    header=bytearray([0x40,1,1,255,1,255,seq&255,seq>>8,0,4,len(payload)])
    header+=bytes.fromhex(uid)+bytes.fromhex(root)+bytes.fromhex(TAG)+struct.pack('<I',boot)
    header+=bytes.fromhex(uid)+struct.pack('<I',boot)+(seq+1).to_bytes(3,'little')
    if key:
        sec=struct.pack('<I',1000)
        header+=sec+protocol._aesccm(key).encrypt(bytes(header[35:48]),payload,bytes(header)+sec)
    else: header+=payload
    header+=struct.pack('<H',protocol.crc16_modbus(header))
    return bytes(header)


class IdentityTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.path=Path(self.tmp.name)/'state.db'
        self.buf=GatewayBuffer(str(self.path)); self.addCleanup(self.buf.close)
        self.wire=identity.WireIdentity(self.buf.conn)
        self.catalog=dict(device_uid=NODE,catalog_tag=TAG,reads=[dict(id='temp',name='Temperatura',unit='C')])
        self.buf.catalog_upsert(1,self.catalog)

    def parsed(self,**kwargs):
        return protocol.parse_frame(uplink(self.wire.uid,**kwargs),kwargs.get('key'))

    def test_development_rows_are_archived_without_incident_or_operational_backlog(self):
        self.assertTrue(self.wire.accept(self.parsed()))
        self.buf.conn.execute("INSERT INTO buffer(origin_id,ts,seq,t_recv,reads_json) VALUES(2,100,1,100,'[20]')")
        identity.incident(self.buf.conn,'legacy_pending',0,'Datos de desarrollo')
        identity.incident(self.buf.conn,'unverified',2,'Formato de desarrollo')
        self.buf.conn.commit(); self.buf.close()
        self.buf=GatewayBuffer(str(self.path)); self.addCleanup(self.buf.close)
        self.assertEqual(self.buf.conn.execute('SELECT count(*) FROM buffer').fetchone()[0],0)
        self.assertEqual(self.buf.conn.execute('SELECT reads_json FROM buffer_development_archive').fetchone()[0],'[20]')
        self.assertEqual(self.buf.conn.execute('SELECT count(*) FROM identity_incidents WHERE active=1').fetchone()[0],0)

    def test_configuration_clone_is_latched_across_restart(self):
        self.assertTrue(self.wire.accept(self.parsed()))
        self.assertFalse(self.wire.accept(self.parsed(uid=OTHER)))
        self.assertTrue(identity.blocked(self.buf.conn,1))
        self.buf.close()
        self.buf=GatewayBuffer(str(self.path)); self.addCleanup(self.buf.close)
        self.assertTrue(identity.blocked(self.buf.conn,1))
        self.assertFalse(identity.observe(self.buf.conn,1,NODE,2))
        self.assertEqual(self.buf.conn.execute('SELECT count(*) FROM identity_incidents').fetchone()[0],1)

    def test_quarantine_keeps_hashes_without_configuration_secrets(self):
        identity.quarantine(self.buf.conn,dict(origin_id=1,payload=b'secret-config',config={'password':'secret-config'}),'identity_unverified')
        saved=self.buf.conn.execute('SELECT payload_json FROM identity_quarantine').fetchone()[0]
        self.assertNotIn('secret-config',saved)
        self.assertIn(hashlib.sha256(b'secret-config').hexdigest(),saved)

    def test_verified_catalog_recovers_only_missing_identity_incidents(self):
        for kind in ('unverified','catalog_missing','node_conflict'):
            identity.incident(self.buf.conn,kind,1,'pendiente')
        self.buf.catalog_upsert(1,self.catalog)
        self.assertEqual(self.buf.conn.execute('SELECT kind FROM identity_incidents WHERE active=1').fetchall(),[('node_conflict',)])

    def test_cloned_configuration_cannot_replace_catalog_or_data(self):
        self.assertTrue(self.wire.accept(self.parsed()))
        self.assertTrue(self.buf.accept(self.parsed(),-60,8))
        self.assertFalse(self.wire.accept(self.parsed(uid=OTHER,value=99)))
        self.assertEqual(self.buf.catalog_get(1)['device_uid'],NODE)
        self.assertEqual(self.buf.fetch_pending()[0]['v'],[21.0])

    def test_ack_is_bound_to_hardware_boot_and_capture(self):
        self.assertTrue(self.wire.accept(self.parsed(boot=9)))
        ack=protocol.build_ack(1,1,7,0,1,1,4)
        encoded=protocol.identity_frame(ack,self.wire,confirmed=self.parsed(boot=9))
        parsed=protocol.parse_frame(encoded)
        self.assertEqual((parsed['dest_uid'],parsed['ack_boot'],parsed['ack_ts']),(NODE,9,1000))

    def test_service_cannot_transmit_a_command_to_a_conflicted_id(self):
        from gateway_service import GatewayService
        self.wire.accept(self.parsed())
        self.wire.accept(self.parsed(uid=OTHER))
        service=GatewayService.__new__(GatewayService)
        service.buf=self.buf; service.identity=self.wire; service.sec_key=None
        service.cfg_tx=service.cfg_rx=service.bcast=None
        frame=protocol.build_node_ping(1,1,1,1,1,1,4)
        self.assertFalse(service._tx(frame))

    def test_other_network_cannot_claim_an_identity_or_raise_a_conflict(self):
        from gateway_service import GatewayService
        service=GatewayService.__new__(GatewayService)
        service.buf=self.buf; service.identity=self.wire; service.sec_key=None
        service.net_id=1; service.n_rx=0
        foreign=bytearray(uplink(self.wire.uid,uid=OTHER))
        foreign[1]=2;foreign[-2:]=struct.pack('<H',protocol.crc16_modbus(foreign[:-2]))
        service.handle_rx_line('[rx] #1 len='+str(len(foreign))+' rssi=-60 snr=8 hex='+foreign.hex())
        self.assertIsNone(self.buf.conn.execute('SELECT uid FROM identity_bindings WHERE origin=1').fetchone())
        self.assertEqual(self.buf.conn.execute('SELECT count(*) FROM identity_incidents').fetchone()[0],0)

    def test_service_cancels_a_queued_incompatible_image(self):
        from gateway_service import GatewayService
        service=GatewayService.__new__(GatewayService)
        service.buf=self.buf; service.identity=self.wire
        service.cfg_tx=service.cfg_rx=service.bcast=None
        self.buf.conn.execute("INSERT INTO fw_bcast(xfer,path,version,total_len,sha256,block_k,block_r,state,created_ts,updated_ts) VALUES(1,'old','0.0.81',160,?,128,10,'pending',1,1)",('00'*32,))
        self.buf.conn.commit()
        service._identity_operations_blocked()
        self.assertEqual(self.buf.conn.execute('SELECT state FROM fw_bcast').fetchone()[0],'failed')

    def test_cipher_authenticates_physical_destination(self):
        key=bytes(range(16)); frame=uplink(self.wire.uid,key=key)
        parsed=protocol.parse_frame(frame,key)
        self.assertNotIn('error',parsed)
        changed=bytearray(frame); changed[17]^=1
        changed[-2:]=struct.pack('<H',protocol.crc16_modbus(changed[:-2]))
        self.assertTrue(protocol.parse_frame(bytes(changed),key)['mic_fail'])

    def test_clock_or_seq_reset_does_not_merge_boots(self):
        for boot,value in ((1,21.0),(2,22.0)):
            parsed=self.parsed(boot=boot,value=value)
            self.assertTrue(self.wire.accept(parsed))
            self.assertTrue(self.buf.accept(parsed,-60,8))
        self.assertEqual(len(self.buf.fetch_pending()),2)
        first=self.buf.fetch_pending()[0]
        self.buf.mark_published([(1,1000,7,NODE,first['boot_id'])])
        self.assertEqual(len(self.buf.fetch_pending()),1)

    def test_same_sample_with_changed_values_is_quarantined(self):
        first=self.parsed(); self.assertTrue(self.wire.accept(first))
        self.assertTrue(self.buf.accept(first,-60,8))
        self.assertIsNone(self.buf.accept(self.parsed(value=99),-60,8))
        self.assertEqual(self.buf.fetch_pending()[0]['v'],[21.0])
        self.assertEqual(self.buf.conn.execute('SELECT kind FROM identity_incidents').fetchone()[0],'sample_conflict')

    def test_assignment_is_reserved_even_when_equipment_is_offline(self):
        self.assertTrue(self.wire.accept(self.parsed()))
        self.buf.conn.execute('BEGIN IMMEDIATE')
        with self.assertRaises(ValueError): identity.check_assignment(self.buf.conn,1,OTHER)
        self.buf.conn.rollback()
        identity.check_assignment(self.buf.conn,2,OTHER); self.buf.conn.commit()
        with self.assertRaises(ValueError): identity.check_assignment(self.buf.conn,2,NODE)

    def test_buffer_restore_does_not_reuse_crypto_session(self):
        old=self.wire.boot
        self.buf.conn.execute("DELETE FROM identity_meta WHERE key='tx_boot'")
        self.buf.conn.commit()
        restarted=identity.WireIdentity(self.buf.conn)
        self.assertGreater(restarted.boot,old)
        nonces=[]
        for clock in (2000,1000,1000):
            encoded=protocol.identity_frame(protocol.build_beacon(1,1,4,clock,bytes(16),clock),restarted,bytes(16))
            nonces.append(encoded[35:48])
        self.assertEqual(len(set(nonces)),3)

    def test_legacy_frame_is_never_misrepresented_as_verified(self):
        raw=protocol.build_beacon(1,1,4,1000)
        self.assertFalse(self.wire.accept(protocol.parse_frame(raw)))
        self.assertIn('error',protocol.parse_frame(raw))
        self.assertEqual(self.buf.conn.execute('SELECT count(*) FROM identity_quarantine').fetchone()[0],0)
        self.assertEqual(self.buf.conn.execute('SELECT count(*) FROM identity_incidents').fetchone()[0],0)

    def test_catalog_versions_survive_replacement(self):
        newer=dict(self.catalog,catalog_tag='aabbccddeeff0011',reads=[dict(id='temp',name='Temperatura',unit='F')])
        self.buf.catalog_upsert(1,newer)
        self.assertEqual(self.buf.conn.execute('SELECT count(*) FROM identity_catalogs').fetchone()[0],2)

    def test_second_gateway_blocks_all_destinations(self):
        identity.observe(self.buf.conn,255,OTHER,1); self.buf.conn.commit()
        self.assertTrue(identity.blocked(self.buf.conn,1))
        with self.assertRaises(ValueError): protocol.identity_frame(protocol.build_beacon(1,1,4,1000),self.wire)

    def test_quarantine_does_not_grow_for_the_same_retry(self):
        parsed=self.parsed(uid=OTHER)
        for _ in range(3): identity.quarantine(self.buf.conn,parsed,'test_conflict')
        self.assertEqual(self.buf.conn.execute('SELECT count(*) FROM identity_quarantine').fetchone()[0],1)


if __name__=='__main__': unittest.main()

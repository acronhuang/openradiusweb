import React, { useState, useEffect } from 'react';
import {
  Table, Tag, Button, Space, Typography, Card, Badge, Modal,
  Form, Input, Select, Switch as AntSwitch, DatePicker, message, Popconfirm,
  Upload, Alert, Tabs,
} from 'antd';
import {
  PlusOutlined, ReloadOutlined, DeleteOutlined, EditOutlined,
  ImportOutlined, DownloadOutlined,
} from '@ant-design/icons';
import type { ColumnsType } from 'antd/es/table';
import type { UploadProps } from 'antd';
import dayjs from 'dayjs';
import api, { extractErrorMessage } from '../../api';

const { Title, Paragraph } = Typography;
const { TextArea } = Input;

// ---------------------------------------------------------------------------
// CSV import helpers — pre-validation runs in the browser so the operator
// sees errors before we send anything to the gateway. Backend re-validates.
// Match services/gateway/features/mab_devices/service.py:CSV_KNOWN_HEADERS.
// ---------------------------------------------------------------------------

const CSV_KNOWN_HEADERS = [
  'mac_address', 'name', 'description', 'device_type',
  'assigned_vlan_id', 'expiry_date',
] as const;
const CSV_REQUIRED_HEADERS = ['mac_address'];

const CSV_SAMPLE =
  'mac_address,name,description,device_type,assigned_vlan_id,expiry_date\n' +
  'aa:bb:cc:11:22:33,Printer-Lobby,"Brother HL-L2310D, lobby",printer,30,\n' +
  'aa:bb:cc:11:22:34,IPCam-MeetingRoom-3F,,camera,30,2027-01-01T00:00:00Z\n';

interface CsvPreviewRow {
  rowIdx: number;
  mac_address?: string;
  name?: string;
  description?: string;
  device_type?: string;
  assigned_vlan_id?: string;
  expiry_date?: string;
  error?: string;
}

interface ImportResult {
  created: number;
  skipped: number;
  total: number;
  parse_errors: { row: number; raw: string; error: string }[];
}

function normaliseMac(raw: string): string | null {
  const hex = raw.replace(/[^0-9a-fA-F]/g, '');
  if (hex.length !== 12) return null;
  return hex.toLowerCase().match(/.{2}/g)!.join(':');
}

/**
 * Parse a CSV blob into preview rows + a top-level error if the file
 * is structurally broken (no header, missing required column).
 * Per-row errors are attached to each preview row instead of failing
 * the whole parse — operators want to see the 47 valid rows alongside
 * the 3 typos.
 */
function parseCsv(text: string): { rows: CsvPreviewRow[]; topError?: string } {
  const lines = text.split(/\r?\n/).filter(line => line.trim().length > 0);
  if (lines.length === 0) {
    return { rows: [], topError: 'CSV is empty' };
  }
  // Minimal CSV parser — splits on commas but respects quoted fields.
  // Good enough for the headers + values we accept (no embedded
  // newlines in cells; that's documented as unsupported).
  const splitRow = (row: string): string[] => {
    const out: string[] = [];
    let cur = '';
    let inQuotes = false;
    for (let i = 0; i < row.length; i++) {
      const ch = row[i];
      if (inQuotes) {
        if (ch === '"' && row[i + 1] === '"') { cur += '"'; i++; }
        else if (ch === '"') { inQuotes = false; }
        else { cur += ch; }
      } else {
        if (ch === ',') { out.push(cur); cur = ''; }
        else if (ch === '"') { inQuotes = true; }
        else { cur += ch; }
      }
    }
    out.push(cur);
    return out.map(s => s.trim());
  };

  const headers = splitRow(lines[0]).map(h => h.toLowerCase());
  const missing = CSV_REQUIRED_HEADERS.filter(h => !headers.includes(h));
  if (missing.length > 0) {
    return {
      rows: [],
      topError: `Missing required column(s): ${missing.join(', ')}`,
    };
  }
  const knownIdx: Partial<Record<typeof CSV_KNOWN_HEADERS[number], number>> = {};
  CSV_KNOWN_HEADERS.forEach(h => {
    const idx = headers.indexOf(h);
    if (idx !== -1) knownIdx[h] = idx;
  });

  const rows: CsvPreviewRow[] = [];
  for (let i = 1; i < lines.length; i++) {
    const cells = splitRow(lines[i]);
    const row: CsvPreviewRow = { rowIdx: i + 1 };  // 1=header, 2=first data
    for (const h of CSV_KNOWN_HEADERS) {
      const idx = knownIdx[h];
      const v = idx !== undefined ? (cells[idx] ?? '').trim() : '';
      if (v) (row as unknown as Record<string, string>)[h] = v;
    }
    // Pre-validate MAC client-side; the backend will re-validate.
    if (!row.mac_address) {
      row.error = 'mac_address is empty';
    } else {
      const norm = normaliseMac(row.mac_address);
      if (!norm) row.error = `Invalid MAC: ${row.mac_address}`;
      else row.mac_address = norm;
    }
    if (!row.error && row.assigned_vlan_id) {
      const n = Number(row.assigned_vlan_id);
      if (!Number.isInteger(n) || n < 1 || n > 4094) {
        row.error = `assigned_vlan_id must be 1-4094, got "${row.assigned_vlan_id}"`;
      }
    }
    rows.push(row);
  }
  return { rows };
}

interface MabDevice {
  id: string;
  mac_address: string;
  name: string;
  description: string;
  device_type: string;
  assigned_vlan_id: number | null;
  enabled: boolean;
  expiry_date: string | null;
  created_at: string;
  updated_at: string;
}

interface VlanOption {
  vlan_id: number;
  name: string;
  purpose: string;
}

const DEVICE_TYPES = [
  { value: 'printer', label: 'Printer' },
  { value: 'camera', label: 'Camera' },
  { value: 'iot', label: 'IoT Sensor' },
  { value: 'phone', label: 'IP Phone' },
  { value: 'sensor', label: 'Sensor' },
  { value: 'ap', label: 'Access Point' },
  { value: 'other', label: 'Other' },
];

const TYPE_COLORS: Record<string, string> = {
  printer: 'cyan',
  camera: 'orange',
  iot: 'green',
  phone: 'purple',
  sensor: 'blue',
  ap: 'geekblue',
  other: 'default',
};

export default function MabDevices() {
  const [devices, setDevices] = useState<MabDevice[]>([]);
  const [vlans, setVlans] = useState<VlanOption[]>([]);
  const [loading, setLoading] = useState(false);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [modalOpen, setModalOpen] = useState(false);
  const [editingDevice, setEditingDevice] = useState<MabDevice | null>(null);
  const [saving, setSaving] = useState(false);
  const [form] = Form.useForm();

  // CSV import modal state — kept separate from the single-device modal
  // because the workflow is different (pre-validation, preview table,
  // post-import results).
  const [csvModalOpen, setCsvModalOpen] = useState(false);
  const [csvText, setCsvText] = useState('');
  const [csvParsed, setCsvParsed] = useState<{ rows: CsvPreviewRow[]; topError?: string } | null>(null);
  const [csvImporting, setCsvImporting] = useState(false);
  const [csvResult, setCsvResult] = useState<ImportResult | null>(null);

  useEffect(() => { loadDevices(); loadVlans(); }, []);

  const loadVlans = async () => {
    try {
      const res = await api.get('/vlans');
      setVlans(res.data.items || []);
    } catch { /* ignore */ }
  };

  const loadDevices = async (p = 1) => {
    setLoading(true);
    try {
      const res = await api.get('/mab-devices', { params: { page: p, page_size: 50 } });
      setDevices(res.data.items || []);
      setTotal(res.data.total || 0);
      setPage(p);
    } catch (err) { message.error(extractErrorMessage(err, 'Failed to load MAB devices')); }
    setLoading(false);
  };

  const openCreate = () => {
    setEditingDevice(null);
    form.resetFields();
    form.setFieldsValue({ enabled: true, device_type: 'other' });
    setModalOpen(true);
  };

  const openEdit = (dev: MabDevice) => {
    setEditingDevice(dev);
    form.setFieldsValue({
      mac_address: dev.mac_address,
      name: dev.name,
      description: dev.description,
      device_type: dev.device_type,
      assigned_vlan_id: dev.assigned_vlan_id,
      enabled: dev.enabled,
      expiry_date: dev.expiry_date ? dayjs(dev.expiry_date) : null,
    });
    setModalOpen(true);
  };

  const handleSave = async () => {
    try {
      const values = await form.validateFields();
      setSaving(true);
      const payload = {
        ...values,
        expiry_date: values.expiry_date ? values.expiry_date.toISOString() : null,
      };
      if (editingDevice) {
        delete payload.mac_address;
        await api.put(`/mab-devices/${editingDevice.id}`, payload);
        message.success('MAB device updated');
      } else {
        await api.post('/mab-devices', payload);
        message.success('MAB device added');
      }
      setModalOpen(false);
      loadDevices(page);
    } catch (err) {
      message.error(extractErrorMessage(err, 'Failed to save MAB device'));
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async (id: string) => {
    try {
      await api.delete(`/mab-devices/${id}`);
      message.success('MAB device removed');
      loadDevices(page);
    } catch (err) { message.error(extractErrorMessage(err, 'Delete failed')); }
  };

  // -------------------------------------------------------------------------
  // CSV import / export
  // -------------------------------------------------------------------------

  const openCsvImport = () => {
    setCsvText('');
    setCsvParsed(null);
    setCsvResult(null);
    setCsvModalOpen(true);
  };

  const handleCsvTextChange = (text: string) => {
    setCsvText(text);
    setCsvResult(null);
    setCsvParsed(text.trim() ? parseCsv(text) : null);
  };

  const csvFileUploadProps: UploadProps = {
    accept: '.csv,text/csv,text/plain',
    showUploadList: false,
    beforeUpload: (file) => {
      const reader = new FileReader();
      reader.onload = (e) => {
        const text = String(e.target?.result || '');
        handleCsvTextChange(text);
      };
      reader.readAsText(file);
      return false; // don't actually upload via Ant's request
    },
  };

  const handleCsvImport = async () => {
    if (!csvText.trim() || !csvParsed || csvParsed.topError) {
      message.error('Fix CSV errors before importing');
      return;
    }
    setCsvImporting(true);
    try {
      const res = await api.post<ImportResult>(
        '/mab-devices/import-csv', csvText,
        { headers: { 'Content-Type': 'text/csv' } },
      );
      setCsvResult(res.data);
      const { created, skipped, parse_errors } = res.data;
      const errCount = parse_errors?.length || 0;
      if (errCount === 0) {
        message.success(`Imported ${created} (${skipped} duplicates skipped)`);
      } else {
        message.warning(
          `Imported ${created} (${skipped} duplicates, ${errCount} errors)`,
        );
      }
      loadDevices(page);
    } catch (err) {
      message.error(extractErrorMessage(err, 'CSV import failed'));
    } finally {
      setCsvImporting(false);
    }
  };

  const handleCsvExport = async () => {
    try {
      const res = await api.get('/mab-devices/export-csv', {
        responseType: 'blob',
      });
      const blob = new Blob([res.data], { type: 'text/csv' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `mab-devices-${dayjs().format('YYYYMMDD-HHmmss')}.csv`;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
    } catch (err) {
      message.error(extractErrorMessage(err, 'Export failed'));
    }
  };

  const handleSampleDownload = () => {
    const blob = new Blob([CSV_SAMPLE], { type: 'text/csv' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = 'mab-devices-sample.csv';
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  };

  // Stats for the import preview banner
  const csvValidCount = csvParsed?.rows.filter(r => !r.error).length || 0;
  const csvErrorCount = csvParsed?.rows.filter(r => r.error).length || 0;

  const getVlanLabel = (vlanId: number | null) => {
    if (!vlanId) return '-';
    const v = vlans.find(vl => vl.vlan_id === vlanId);
    return v ? `${vlanId} - ${v.name}` : String(vlanId);
  };

  const columns: ColumnsType<MabDevice> = [
    {
      title: 'MAC Address', dataIndex: 'mac_address', width: 170,
      render: (mac, record) => (
        <a onClick={() => openEdit(record)} style={{ fontFamily: 'monospace' }}>{mac}</a>
      ),
    },
    { title: 'Name', dataIndex: 'name', width: 180, render: (v) => v || '-' },
    {
      title: 'Device Type', dataIndex: 'device_type', width: 120,
      render: (v) => v ? <Tag color={TYPE_COLORS[v] || 'default'}>{v}</Tag> : '-',
    },
    {
      title: 'Assigned VLAN', dataIndex: 'assigned_vlan_id', width: 150,
      render: (v) => v ? <Tag color="blue">{getVlanLabel(v)}</Tag> : '-',
    },
    {
      title: 'Enabled', dataIndex: 'enabled', width: 80,
      render: (v) => v ? <Badge status="success" text="Yes" /> : <Badge status="default" text="No" />,
    },
    {
      title: 'Expiry', dataIndex: 'expiry_date', width: 120,
      render: (v) => {
        if (!v) return <Tag>Never</Tag>;
        const d = dayjs(v);
        const expired = d.isBefore(dayjs());
        return <Tag color={expired ? 'red' : 'default'}>{d.format('YYYY-MM-DD')}</Tag>;
      },
    },
    {
      title: 'Actions', key: 'actions', width: 100,
      render: (_, record) => (
        <Space>
          <Button size="small" icon={<EditOutlined />} onClick={() => openEdit(record)} />
          <Popconfirm title="Remove from MAB whitelist?" onConfirm={() => handleDelete(record.id)}>
            <Button size="small" danger icon={<DeleteOutlined />} />
          </Popconfirm>
        </Space>
      ),
    },
  ];

  return (
    <div>
      <Title level={4}>MAB Device Whitelist</Title>
      <Card>
        <Space style={{ marginBottom: 16 }} wrap>
          <Button type="primary" icon={<PlusOutlined />} onClick={openCreate}>Add Device</Button>
          <Button icon={<ImportOutlined />} onClick={openCsvImport}>Import CSV</Button>
          <Button icon={<DownloadOutlined />} onClick={handleCsvExport}>Export CSV</Button>
          <Button icon={<ReloadOutlined />} onClick={() => loadDevices(page)}>Refresh</Button>
        </Space>
        <Table
          columns={columns}
          dataSource={devices}
          rowKey="id"
          loading={loading}
          size="small"
          pagination={{
            current: page,
            total,
            pageSize: 50,
            onChange: (p) => loadDevices(p),
            showTotal: (t) => `Total: ${t}`,
          }}
        />
      </Card>

      <Modal
        title={editingDevice ? 'Edit MAB Device' : 'Add MAB Device'}
        open={modalOpen}
        onCancel={() => setModalOpen(false)}
        onOk={handleSave}
        confirmLoading={saving}
        width={520}
        destroyOnClose
      >
        <Form form={form} layout="vertical" size="small">
          <Form.Item name="mac_address" label="MAC Address"
            rules={[{ required: true, message: 'MAC address is required' }]}>
            <Input
              placeholder="AA:BB:CC:DD:EE:FF"
              disabled={!!editingDevice}
              style={{ fontFamily: 'monospace' }}
            />
          </Form.Item>
          <Form.Item name="name" label="Name">
            <Input placeholder="e.g., Office Printer 3F" />
          </Form.Item>
          <Space style={{ display: 'flex' }} align="start">
            <Form.Item name="device_type" label="Device Type">
              <Select style={{ width: 160 }}>
                {DEVICE_TYPES.map(t => (
                  <Select.Option key={t.value} value={t.value}>{t.label}</Select.Option>
                ))}
              </Select>
            </Form.Item>
            <Form.Item name="assigned_vlan_id" label="Assigned VLAN">
              <Select placeholder="Select VLAN" allowClear style={{ width: 200 }}>
                {vlans.map(v => (
                  <Select.Option key={v.vlan_id} value={v.vlan_id}>
                    {v.vlan_id} - {v.name}
                  </Select.Option>
                ))}
              </Select>
            </Form.Item>
          </Space>
          <Form.Item name="description" label="Description">
            <TextArea rows={2} placeholder="Optional description" />
          </Form.Item>
          <Space style={{ display: 'flex' }} align="start">
            <Form.Item name="enabled" label="Enabled" valuePropName="checked">
              <AntSwitch />
            </Form.Item>
            <Form.Item name="expiry_date" label="Expiry Date">
              <DatePicker style={{ width: 200 }} />
            </Form.Item>
          </Space>
        </Form>
      </Modal>

      <Modal
        title="Import MAB Devices from CSV"
        open={csvModalOpen}
        onCancel={() => setCsvModalOpen(false)}
        onOk={handleCsvImport}
        confirmLoading={csvImporting}
        okText="Import"
        okButtonProps={{
          disabled: !csvParsed || !!csvParsed.topError || csvValidCount === 0,
        }}
        width={780}
        destroyOnClose
      >
        <Paragraph type="secondary">
          Required column: <code>mac_address</code>. Optional:{' '}
          <code>name</code>, <code>description</code>, <code>device_type</code>,{' '}
          <code>assigned_vlan_id</code>, <code>expiry_date</code> (ISO 8601).
          Header order doesn&apos;t matter; unknown columns are ignored.
          Duplicate MACs are silently skipped.{' '}
          <a onClick={handleSampleDownload}>Download sample CSV</a>
        </Paragraph>

        <Tabs
          size="small"
          items={[
            {
              key: 'paste',
              label: 'Paste CSV',
              children: (
                <TextArea
                  rows={8}
                  value={csvText}
                  onChange={(e) => handleCsvTextChange(e.target.value)}
                  placeholder={'mac_address,name,device_type,assigned_vlan_id\naa:bb:cc:dd:ee:01,Printer-Lobby,printer,30\n…'}
                  style={{ fontFamily: 'monospace', fontSize: 12 }}
                />
              ),
            },
            {
              key: 'upload',
              label: 'Upload .csv file',
              children: (
                <Upload.Dragger {...csvFileUploadProps}>
                  <p className="ant-upload-drag-icon"><ImportOutlined /></p>
                  <p className="ant-upload-text">Click or drag a .csv file here</p>
                  <p className="ant-upload-hint">
                    The file is parsed locally — nothing uploads until you click Import.
                  </p>
                </Upload.Dragger>
              ),
            },
          ]}
        />

        {csvParsed?.topError && (
          <Alert
            type="error" showIcon style={{ marginTop: 12 }}
            message="CSV format error"
            description={csvParsed.topError}
          />
        )}

        {csvParsed && !csvParsed.topError && (
          <div style={{ marginTop: 12 }}>
            <Alert
              type={csvErrorCount === 0 ? 'success' : 'warning'}
              showIcon
              message={`Pre-validation: ${csvValidCount} valid, ${csvErrorCount} errors`}
              description={
                csvErrorCount > 0
                  ? 'Rows with errors will be skipped; valid rows still import.'
                  : 'Click Import to send to the server.'
              }
            />
            <Table
              size="small"
              style={{ marginTop: 8 }}
              dataSource={csvParsed.rows.slice(0, 50)}
              rowKey="rowIdx"
              pagination={false}
              scroll={{ y: 220 }}
              columns={[
                { title: 'Row', dataIndex: 'rowIdx', width: 50 },
                { title: 'MAC', dataIndex: 'mac_address', width: 150,
                  render: (v) => <span style={{ fontFamily: 'monospace' }}>{v || '-'}</span> },
                { title: 'Name', dataIndex: 'name', width: 130, render: (v) => v || '-' },
                { title: 'Type', dataIndex: 'device_type', width: 80, render: (v) => v || '-' },
                { title: 'VLAN', dataIndex: 'assigned_vlan_id', width: 60, render: (v) => v || '-' },
                { title: 'Expiry', dataIndex: 'expiry_date', width: 110, render: (v) => v || '-' },
                {
                  title: 'Status', dataIndex: 'error',
                  render: (v) => v
                    ? <Tag color="red">{v}</Tag>
                    : <Tag color="green">OK</Tag>,
                },
              ]}
            />
            {csvParsed.rows.length > 50 && (
              <Paragraph type="secondary" style={{ marginTop: 4 }}>
                Showing first 50 of {csvParsed.rows.length} rows. All rows
                will be imported.
              </Paragraph>
            )}
          </div>
        )}

        {csvResult && (
          <Alert
            type={csvResult.parse_errors.length === 0 ? 'success' : 'warning'}
            showIcon style={{ marginTop: 12 }}
            message={`Import complete: ${csvResult.created} created, ${csvResult.skipped} duplicates skipped, ${csvResult.parse_errors.length} parse errors`}
            description={csvResult.parse_errors.length > 0 && (
              <ul style={{ margin: '4px 0 0 16px', maxHeight: 120, overflow: 'auto' }}>
                {csvResult.parse_errors.map((e, i) => (
                  <li key={i}>Row {e.row}: {e.error}</li>
                ))}
              </ul>
            )}
          />
        )}
      </Modal>
    </div>
  );
}

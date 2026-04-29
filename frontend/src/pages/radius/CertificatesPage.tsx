import React, { useState, useEffect } from 'react';
import {
  Table, Tag, Button, Space, Typography, Card, Badge, Modal,
  Form, Input, InputNumber, Select, message, Popconfirm,
  Descriptions, Row, Col, Alert,
} from 'antd';
import {
  PlusOutlined, ReloadOutlined, DeleteOutlined,
  SafetyCertificateOutlined, DownloadOutlined, ImportOutlined,
  CheckCircleOutlined, MinusCircleOutlined,
} from '@ant-design/icons';
import type { ColumnsType } from 'antd/es/table';
import api, { extractErrorMessage } from '../../api';

const { Title } = Typography;
const { TextArea } = Input;

interface Certificate {
  id: string;
  name: string;
  cert_type: 'ca' | 'server';
  common_name: string;
  issuer: string;
  valid_from: string;
  valid_to: string;
  fingerprint: string;
  days_until_expiry: number;
  status: 'valid' | 'expiring_soon' | 'expired';
  is_active: boolean;
  created_at: string;
}

interface CertSummary {
  ca: Certificate | null;
  server: Certificate | null;
}

const STATUS_COLORS: Record<string, string> = {
  valid: 'green',
  expiring_soon: 'orange',
  expired: 'red',
};

const TYPE_COLORS: Record<string, string> = {
  ca: 'purple',
  server: 'blue',
};

export default function CertificatesPage() {
  const [certificates, setCertificates] = useState<Certificate[]>([]);
  const [summary, setSummary] = useState<CertSummary>({ ca: null, server: null });
  const [loading, setLoading] = useState(false);
  const [generateCaOpen, setGenerateCaOpen] = useState(false);
  const [generateServerOpen, setGenerateServerOpen] = useState(false);
  const [importOpen, setImportOpen] = useState(false);
  const [saving, setSaving] = useState(false);
  const [caForm] = Form.useForm();
  const [serverForm] = Form.useForm();
  const [importForm] = Form.useForm();

  useEffect(() => { loadCertificates(); }, []);

  const loadCertificates = async () => {
    setLoading(true);
    try {
      const res = await api.get('/certificates');
      const certs: Certificate[] = res.data.items || res.data || [];
      setCertificates(certs);

      // Find active CA and server certs for summary
      const activeCa = certs.find((c) => c.cert_type === 'ca' && c.is_active) || null;
      const activeServer = certs.find((c) => c.cert_type === 'server' && c.is_active) || null;
      setSummary({ ca: activeCa, server: activeServer });
    } catch (err) {
      message.error(extractErrorMessage(err, 'Failed to load certificates'));
    }
    setLoading(false);
  };

  const hasExpiringCerts = certificates.some(
    (c) => c.is_active && c.days_until_expiry < 30 && c.days_until_expiry >= 0
  );

  // Generate CA
  const openGenerateCa = () => {
    caForm.resetFields();
    caForm.setFieldsValue({
      validity_days: 3650,
      key_size: 4096,
    });
    setGenerateCaOpen(true);
  };

  const handleGenerateCa = async () => {
    try {
      const values = await caForm.validateFields();
      setSaving(true);
      await api.post('/certificates/generate-ca', values);
      message.success('CA certificate generated');
      setGenerateCaOpen(false);
      loadCertificates();
    } catch (err) {
      message.error(extractErrorMessage(err, 'Failed to generate CA certificate'));
    } finally {
      setSaving(false);
    }
  };

  // Generate Server
  const openGenerateServer = () => {
    serverForm.resetFields();
    serverForm.setFieldsValue({
      validity_days: 730,
      key_size: 2048,
      san_dns: [''],
      san_ips: [],
    });
    setGenerateServerOpen(true);
  };

  const handleGenerateServer = async () => {
    try {
      const values = await serverForm.validateFields();
      setSaving(true);

      const payload = {
        ...values,
        san_dns: (values.san_dns || []).filter((v: string) => v && v.trim()),
        san_ips: (values.san_ips || []).filter((v: string) => v && v.trim()),
      };

      await api.post('/certificates/generate-server', payload);
      message.success('Server certificate generated');
      setGenerateServerOpen(false);
      loadCertificates();
    } catch (err) {
      message.error(extractErrorMessage(err, 'Failed to generate server certificate'));
    } finally {
      setSaving(false);
    }
  };

  // Import
  const openImport = () => {
    importForm.resetFields();
    importForm.setFieldsValue({ cert_type: 'server' });
    setImportOpen(true);
  };

  const handleImport = async () => {
    try {
      const values = await importForm.validateFields();
      setSaving(true);
      await api.post('/certificates/import', values);
      message.success('Certificate imported');
      setImportOpen(false);
      loadCertificates();
    } catch (err) {
      message.error(extractErrorMessage(err, 'Failed to import certificate'));
    } finally {
      setSaving(false);
    }
  };

  // Activate
  const handleActivate = async (id: string) => {
    try {
      await api.put(`/certificates/${id}/activate`);
      message.success('Certificate activated');
      loadCertificates();
    } catch (err) {
      message.error(extractErrorMessage(err, 'Activation failed'));
    }
  };

  // Download via API with Authorization header (no token in URL)
  const handleDownload = async (id: string) => {
    try {
      const res = await api.get(`/certificates/${id}/download`, { responseType: 'blob' });
      const disposition = res.headers['content-disposition'] || '';
      const filenameMatch = disposition.match(/filename="?([^";\n]+)"?/);
      const filename = filenameMatch ? filenameMatch[1] : `certificate-${id}.pem`;
      const url = window.URL.createObjectURL(new Blob([res.data]));
      const link = document.createElement('a');
      link.href = url;
      link.download = filename;
      document.body.appendChild(link);
      link.click();
      link.remove();
      window.URL.revokeObjectURL(url);
    } catch (err) {
      message.error(extractErrorMessage(err, 'Download failed'));
    }
  };

  // Delete
  const handleDelete = async (id: string) => {
    try {
      await api.delete(`/certificates/${id}`);
      message.success('Certificate deleted');
      loadCertificates();
    } catch (err) {
      message.error(extractErrorMessage(err, 'Delete failed'));
    }
  };

  const renderCertSummary = (cert: Certificate | null, label: string) => {
    if (!cert) {
      return (
        <Card title={label} style={{ height: '100%' }}>
          <Typography.Text type="secondary">No {label.toLowerCase()} configured</Typography.Text>
        </Card>
      );
    }
    return (
      <Card title={label} style={{ height: '100%' }}>
        <Descriptions column={1} size="small">
          <Descriptions.Item label="Common Name">{cert.common_name}</Descriptions.Item>
          <Descriptions.Item label="Issuer">{cert.issuer}</Descriptions.Item>
          <Descriptions.Item label="Valid From">
            {cert.valid_from ? new Date(cert.valid_from).toLocaleDateString() : '-'}
          </Descriptions.Item>
          <Descriptions.Item label="Valid To">
            {cert.valid_to ? new Date(cert.valid_to).toLocaleDateString() : '-'}
            {cert.days_until_expiry !== undefined && (
              <Tag color={cert.days_until_expiry < 30 ? 'red' : 'green'} style={{ marginLeft: 8 }}>
                {cert.days_until_expiry} days
              </Tag>
            )}
          </Descriptions.Item>
          <Descriptions.Item label="Fingerprint">
            <Typography.Text code copyable style={{ fontSize: 11 }}>
              {cert.fingerprint}
            </Typography.Text>
          </Descriptions.Item>
        </Descriptions>
      </Card>
    );
  };

  const columns: ColumnsType<Certificate> = [
    {
      title: 'Type', dataIndex: 'cert_type', width: 90,
      render: (v) => <Tag color={TYPE_COLORS[v] || 'default'}>{v?.toUpperCase()}</Tag>,
    },
    { title: 'Name', dataIndex: 'name', width: 180 },
    { title: 'Common Name', dataIndex: 'common_name', width: 200 },
    {
      title: 'Expires', key: 'expires', width: 180,
      render: (_, record) => (
        <Space>
          {record.valid_to ? new Date(record.valid_to).toLocaleDateString() : '-'}
          {record.days_until_expiry !== undefined && (
            <Tag color={record.days_until_expiry < 30 ? 'red' : record.days_until_expiry < 90 ? 'orange' : 'green'}>
              {record.days_until_expiry}d
            </Tag>
          )}
        </Space>
      ),
    },
    {
      title: 'Status', dataIndex: 'status', width: 110,
      render: (v) => <Tag color={STATUS_COLORS[v] || 'default'}>{v}</Tag>,
    },
    {
      title: 'Active', dataIndex: 'is_active', width: 80,
      render: (v) => v
        ? <Badge status="success" text="Active" />
        : <Badge status="default" text="No" />,
    },
    {
      title: 'Actions', key: 'actions', width: 160,
      render: (_, record) => (
        <Space>
          {!record.is_active && (
            <Popconfirm title="Activate this certificate?" onConfirm={() => handleActivate(record.id)}>
              <Button size="small" icon={<CheckCircleOutlined />} title="Activate" />
            </Popconfirm>
          )}
          <Button size="small" icon={<DownloadOutlined />} onClick={() => handleDownload(record.id)} title="Download" />
          <Popconfirm title="Delete this certificate?" onConfirm={() => handleDelete(record.id)}>
            <Button size="small" danger icon={<DeleteOutlined />} />
          </Popconfirm>
        </Space>
      ),
    },
  ];

  return (
    <div>
      <Title level={4}>Certificates</Title>

      {hasExpiringCerts && (
        <Alert
          message="Certificate Expiry Warning"
          description="One or more active certificates will expire within 30 days. Please renew them to avoid service disruption."
          type="warning"
          showIcon
          closable
          style={{ marginBottom: 16 }}
        />
      )}

      <Row gutter={16} style={{ marginBottom: 16 }}>
        <Col span={12}>{renderCertSummary(summary.ca, 'CA Certificate')}</Col>
        <Col span={12}>{renderCertSummary(summary.server, 'Server Certificate')}</Col>
      </Row>

      <Card>
        <Space style={{ marginBottom: 16 }}>
          <Button type="primary" icon={<SafetyCertificateOutlined />} onClick={openGenerateCa}>
            Generate CA
          </Button>
          <Button type="primary" icon={<SafetyCertificateOutlined />} onClick={openGenerateServer}>
            Generate Server Cert
          </Button>
          <Button icon={<ImportOutlined />} onClick={openImport}>
            Import Certificate
          </Button>
          <Button icon={<ReloadOutlined />} onClick={loadCertificates}>Refresh</Button>
        </Space>
        <Table
          columns={columns}
          dataSource={certificates}
          rowKey="id"
          loading={loading}
          size="small"
          pagination={{ pageSize: 50, showTotal: (t) => `Total: ${t}` }}
        />
      </Card>

      {/* Generate CA Modal */}
      <Modal
        title="Generate CA Certificate"
        open={generateCaOpen}
        onCancel={() => setGenerateCaOpen(false)}
        onOk={handleGenerateCa}
        confirmLoading={saving}
        width={520}
        destroyOnClose
      >
        <Form form={caForm} layout="vertical" size="small">
          <Form.Item name="name" label="Name" rules={[{ required: true, message: 'Name is required' }]}>
            <Input placeholder="e.g., OpenRadius Root CA" />
          </Form.Item>
          <Form.Item name="common_name" label="Common Name" rules={[{ required: true, message: 'CN is required' }]}>
            <Input placeholder="e.g., OpenRadius Root CA" />
          </Form.Item>
          <Space>
            <Form.Item name="validity_days" label="Validity (days)">
              <InputNumber min={365} max={7300} style={{ width: 140 }} />
            </Form.Item>
            <Form.Item name="key_size" label="Key Size">
              <Select style={{ width: 120 }}>
                <Select.Option value={2048}>2048</Select.Option>
                <Select.Option value={4096}>4096</Select.Option>
              </Select>
            </Form.Item>
          </Space>
          <Form.Item name="organization" label="Organization">
            <Input placeholder="e.g., My Company Inc." />
          </Form.Item>
          <Form.Item name="country" label="Country">
            <Input placeholder="e.g., US" maxLength={2} style={{ width: 100 }} />
          </Form.Item>
        </Form>
      </Modal>

      {/* Generate Server Cert Modal */}
      <Modal
        title="Generate Server Certificate"
        open={generateServerOpen}
        onCancel={() => setGenerateServerOpen(false)}
        onOk={handleGenerateServer}
        confirmLoading={saving}
        width={600}
        destroyOnClose
      >
        <Form form={serverForm} layout="vertical" size="small">
          <Form.Item name="name" label="Name" rules={[{ required: true, message: 'Name is required' }]}>
            <Input placeholder="e.g., RADIUS Server Cert" />
          </Form.Item>
          <Form.Item name="common_name" label="Common Name" rules={[{ required: true, message: 'CN is required' }]}>
            <Input placeholder="e.g., radius.example.com" />
          </Form.Item>

          <Form.Item label="DNS Subject Alternative Names">
            <Form.List name="san_dns">
              {(fields, { add, remove }) => (
                <>
                  {fields.map(({ key, name, ...rest }) => (
                    <Space key={key} style={{ display: 'flex', marginBottom: 4 }} align="baseline">
                      <Form.Item {...rest} name={name} style={{ marginBottom: 0 }}>
                        <Input placeholder="e.g., radius.example.com" style={{ width: 360 }} />
                      </Form.Item>
                      <MinusCircleOutlined onClick={() => remove(name)} />
                    </Space>
                  ))}
                  <Button type="dashed" onClick={() => add('')} icon={<PlusOutlined />}
                    style={{ width: '100%' }}>
                    Add DNS SAN
                  </Button>
                </>
              )}
            </Form.List>
          </Form.Item>

          <Form.Item label="IP Subject Alternative Names">
            <Form.List name="san_ips">
              {(fields, { add, remove }) => (
                <>
                  {fields.map(({ key, name, ...rest }) => (
                    <Space key={key} style={{ display: 'flex', marginBottom: 4 }} align="baseline">
                      <Form.Item {...rest} name={name} style={{ marginBottom: 0 }}>
                        <Input placeholder="e.g., 192.168.1.10" style={{ width: 360 }} />
                      </Form.Item>
                      <MinusCircleOutlined onClick={() => remove(name)} />
                    </Space>
                  ))}
                  <Button type="dashed" onClick={() => add('')} icon={<PlusOutlined />}
                    style={{ width: '100%' }}>
                    Add IP SAN
                  </Button>
                </>
              )}
            </Form.List>
          </Form.Item>

          <Space>
            <Form.Item name="validity_days" label="Validity (days)">
              <InputNumber min={30} max={3650} style={{ width: 140 }} />
            </Form.Item>
            <Form.Item name="key_size" label="Key Size">
              <Select style={{ width: 120 }}>
                <Select.Option value={2048}>2048</Select.Option>
                <Select.Option value={4096}>4096</Select.Option>
              </Select>
            </Form.Item>
          </Space>
        </Form>
      </Modal>

      {/* Import Certificate Modal */}
      <Modal
        title="Import Certificate"
        open={importOpen}
        onCancel={() => setImportOpen(false)}
        onOk={handleImport}
        confirmLoading={saving}
        width={640}
        destroyOnClose
      >
        <Form form={importForm} layout="vertical" size="small">
          <Space style={{ display: 'flex' }} align="start">
            <Form.Item name="name" label="Name" rules={[{ required: true, message: 'Name is required' }]}>
              <Input placeholder="e.g., Imported Server Cert" style={{ width: 300 }} />
            </Form.Item>
            <Form.Item name="cert_type" label="Type" rules={[{ required: true }]}>
              <Select style={{ width: 120 }}>
                <Select.Option value="ca">CA</Select.Option>
                <Select.Option value="server">Server</Select.Option>
              </Select>
            </Form.Item>
          </Space>
          <Form.Item name="cert_pem" label="Certificate PEM"
            rules={[{ required: true, message: 'Certificate PEM is required' }]}>
            <TextArea rows={6} placeholder="-----BEGIN CERTIFICATE-----&#10;...&#10;-----END CERTIFICATE-----" />
          </Form.Item>
          <Form.Item name="key_pem" label="Private Key PEM (optional)">
            <TextArea rows={6} placeholder="-----BEGIN PRIVATE KEY-----&#10;...&#10;-----END PRIVATE KEY-----" />
          </Form.Item>
          <Form.Item name="chain_pem" label="Certificate Chain PEM (optional)">
            <TextArea rows={4} placeholder="Intermediate certificate chain (if applicable)" />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  );
}

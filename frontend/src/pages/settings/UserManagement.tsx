import React, { useState, useEffect } from 'react';
import {
  Table, Tag, Button, Space, Typography, Card, Badge, Modal,
  Form, Input, Select, Switch, message, Popconfirm, Tabs,
} from 'antd';
import {
  PlusOutlined, ReloadOutlined, EditOutlined, DeleteOutlined,
  LockOutlined, CheckCircleOutlined, MinusCircleOutlined,
  UserOutlined,
} from '@ant-design/icons';
import type { ColumnsType } from 'antd/es/table';
import api, { extractErrorMessage } from '../../api';

const { Title } = Typography;

interface User {
  id: string;
  username: string;
  email: string;
  role: string;
  enabled: boolean;
  last_login: string | null;
  created_at: string;
}

interface Role {
  name: string;
  permissions: string[];
}

const ROLE_COLORS: Record<string, string> = {
  admin: 'red',
  operator: 'blue',
  viewer: 'green',
};

export default function UserManagement() {
  const [users, setUsers] = useState<User[]>([]);
  const [roles, setRoles] = useState<Role[]>([]);
  const [loading, setLoading] = useState(false);
  const [rolesLoading, setRolesLoading] = useState(false);

  // User modal
  const [userModalOpen, setUserModalOpen] = useState(false);
  const [editingUser, setEditingUser] = useState<User | null>(null);
  const [saving, setSaving] = useState(false);
  const [form] = Form.useForm();

  // Reset password modal
  const [resetModalOpen, setResetModalOpen] = useState(false);
  const [resetUserId, setResetUserId] = useState<string | null>(null);
  const [resetForm] = Form.useForm();

  // Current user (to prevent self-delete)
  const [currentUsername, setCurrentUsername] = useState<string>('');

  useEffect(() => {
    loadUsers();
    loadRoles();
    loadCurrentUser();
  }, []);

  const loadCurrentUser = async () => {
    try {
      const res = await api.get('/auth/me');
      setCurrentUsername(res.data.username || '');
    } catch { message.error('Failed to load users'); }
  };

  const loadUsers = async () => {
    setLoading(true);
    try {
      const res = await api.get('/auth/users');
      setUsers(Array.isArray(res.data) ? res.data : res.data.items || []);
    } catch { message.error('Failed to load users'); }
    setLoading(false);
  };

  const loadRoles = async () => {
    setRolesLoading(true);
    try {
      const res = await api.get('/auth/roles');
      setRoles(Array.isArray(res.data) ? res.data : res.data.items || []);
    } catch { message.error('Failed to load users'); }
    setRolesLoading(false);
  };

  const openCreate = () => {
    setEditingUser(null);
    form.resetFields();
    form.setFieldsValue({ role: 'viewer', enabled: true });
    setUserModalOpen(true);
  };

  const openEdit = (user: User) => {
    setEditingUser(user);
    form.setFieldsValue({
      username: user.username,
      email: user.email,
      role: user.role,
      enabled: user.enabled,
    });
    setUserModalOpen(true);
  };

  const handleSaveUser = async () => {
    try {
      const values = await form.validateFields();
      setSaving(true);
      if (editingUser) {
        await api.put(`/auth/users/${editingUser.id}`, {
          email: values.email,
          role: values.role,
          enabled: values.enabled,
        });
        message.success('User updated');
      } else {
        await api.post('/auth/users', {
          username: values.username,
          email: values.email,
          password: values.password,
          role: values.role,
          enabled: values.enabled,
        });
        message.success('User created');
      }
      setUserModalOpen(false);
      loadUsers();
    } catch (err: any) {
      if (err?.response?.data?.detail) {
        message.error(err.response.data.detail);
      }
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async (id: string) => {
    try {
      await api.delete(`/auth/users/${id}`);
      message.success('User deleted');
      loadUsers();
    } catch (err) {
      message.error(extractErrorMessage(err, 'Delete failed'));
    }
  };

  const handleToggleEnabled = async (user: User, enabled: boolean) => {
    try {
      await api.put(`/auth/users/${user.id}`, { enabled });
      message.success(`User ${enabled ? 'enabled' : 'disabled'}`);
      loadUsers();
    } catch {
      message.error('Update failed');
    }
  };

  const openResetPassword = (userId: string) => {
    setResetUserId(userId);
    resetForm.resetFields();
    setResetModalOpen(true);
  };

  const handleResetPassword = async () => {
    try {
      const values = await resetForm.validateFields();
      await api.post(`/auth/users/${resetUserId}/reset-password`, {
        new_password: values.new_password,
      });
      message.success('Password reset successfully');
      setResetModalOpen(false);
    } catch (err: any) {
      message.error(err?.response?.data?.detail || 'Password reset failed');
    }
  };

  const userColumns: ColumnsType<User> = [
    { title: 'Username', dataIndex: 'username', width: 150 },
    { title: 'Email', dataIndex: 'email', width: 220 },
    {
      title: 'Role', dataIndex: 'role', width: 100,
      render: (v) => <Tag color={ROLE_COLORS[v] || 'default'}>{v}</Tag>,
    },
    {
      title: 'Status', dataIndex: 'enabled', width: 100,
      render: (v) => (
        <Badge status={v ? 'success' : 'default'} text={v ? 'Enabled' : 'Disabled'} />
      ),
    },
    {
      title: 'Last Login', dataIndex: 'last_login', width: 170,
      render: (v) => v ? new Date(v).toLocaleString() : 'Never',
    },
    {
      title: 'Actions', key: 'actions', width: 220,
      render: (_, record) => (
        <Space>
          <Button size="small" icon={<EditOutlined />} onClick={() => openEdit(record)}>
            Edit
          </Button>
          <Button
            size="small"
            onClick={() => handleToggleEnabled(record, !record.enabled)}
          >
            {record.enabled ? 'Disable' : 'Enable'}
          </Button>
          <Button
            size="small"
            icon={<LockOutlined />}
            onClick={() => openResetPassword(record.id)}
          >
            Reset Pwd
          </Button>
          {record.username !== currentUsername && (
            <Popconfirm title="Delete this user?" onConfirm={() => handleDelete(record.id)}>
              <Button size="small" danger icon={<DeleteOutlined />} />
            </Popconfirm>
          )}
        </Space>
      ),
    },
  ];

  // Build permission matrix from roles
  const buildPermissionMatrix = () => {
    const allPermissions = new Set<string>();
    roles.forEach((role) => {
      role.permissions.forEach((p) => allPermissions.add(p));
    });

    const sorted = Array.from(allPermissions).sort();

    // Group by category prefix
    const grouped: Record<string, string[]> = {};
    sorted.forEach((p) => {
      const category = p.split('.')[0] || 'other';
      if (!grouped[category]) grouped[category] = [];
      grouped[category].push(p);
    });

    const rows: { key: string; permission: string; category: string; admin: boolean; operator: boolean; viewer: boolean }[] = [];
    Object.entries(grouped).forEach(([category, perms]) => {
      perms.forEach((perm) => {
        const roleMap: Record<string, boolean> = { admin: false, operator: false, viewer: false };
        roles.forEach((role) => {
          if (['admin', 'operator', 'viewer'].includes(role.name)) {
            roleMap[role.name] = role.permissions.includes(perm) || role.permissions.includes('*');
          }
        });
        rows.push({
          key: perm,
          permission: perm,
          category,
          admin: roleMap.admin,
          operator: roleMap.operator,
          viewer: roleMap.viewer,
        });
      });
    });

    return rows;
  };

  const renderPermissionIcon = (has: boolean) =>
    has
      ? <CheckCircleOutlined style={{ color: '#52c41a', fontSize: 16 }} />
      : <MinusCircleOutlined style={{ color: '#bfbfbf', fontSize: 16 }} />;

  const permissionColumns: ColumnsType<any> = [
    {
      title: 'Category', dataIndex: 'category', width: 120,
      render: (v: string) => <Tag>{v}</Tag>,
      onCell: (_, index) => {
        const data = buildPermissionMatrix();
        if (index === undefined) return {};
        const current = data[index];
        const prev = index > 0 ? data[index - 1] : null;
        if (prev && prev.category === current.category) {
          return { rowSpan: 0 };
        }
        const span = data.filter((r) => r.category === current.category).length;
        return { rowSpan: span };
      },
    },
    { title: 'Permission', dataIndex: 'permission' },
    {
      title: 'Admin', dataIndex: 'admin', width: 100, align: 'center' as const,
      render: (v: boolean) => renderPermissionIcon(v),
    },
    {
      title: 'Operator', dataIndex: 'operator', width: 100, align: 'center' as const,
      render: (v: boolean) => renderPermissionIcon(v),
    },
    {
      title: 'Viewer', dataIndex: 'viewer', width: 100, align: 'center' as const,
      render: (v: boolean) => renderPermissionIcon(v),
    },
  ];

  const tabItems = [
    {
      key: 'users',
      label: (
        <Space><UserOutlined />Users</Space>
      ),
      children: (
        <>
          <Space style={{ marginBottom: 16 }}>
            <Button type="primary" icon={<PlusOutlined />} onClick={openCreate}>
              Create User
            </Button>
            <Button icon={<ReloadOutlined />} onClick={loadUsers}>Refresh</Button>
          </Space>
          <Table
            columns={userColumns}
            dataSource={users}
            rowKey="id"
            loading={loading}
            size="small"
            pagination={{ pageSize: 20, showTotal: (t) => `Total: ${t}` }}
          />
        </>
      ),
    },
    {
      key: 'roles',
      label: 'Role Permissions',
      children: (
        <>
          <Space style={{ marginBottom: 16 }}>
            <Button icon={<ReloadOutlined />} onClick={loadRoles}>Refresh</Button>
          </Space>
          <Table
            columns={permissionColumns}
            dataSource={buildPermissionMatrix()}
            rowKey="key"
            loading={rolesLoading}
            size="small"
            pagination={false}
            bordered
          />
        </>
      ),
    },
  ];

  return (
    <div>
      <Title level={4}>User Management</Title>
      <Card>
        <Tabs items={tabItems} />
      </Card>

      {/* Create / Edit User Modal */}
      <Modal
        title={editingUser ? 'Edit User' : 'Create User'}
        open={userModalOpen}
        onCancel={() => setUserModalOpen(false)}
        onOk={handleSaveUser}
        confirmLoading={saving}
        destroyOnClose
      >
        <Form form={form} layout="vertical">
          <Form.Item
            name="username"
            label="Username"
            rules={[{ required: true, message: 'Username is required' }]}
          >
            <Input disabled={!!editingUser} placeholder="Username" />
          </Form.Item>
          <Form.Item
            name="email"
            label="Email"
            rules={[
              { required: true, message: 'Email is required' },
              { type: 'email', message: 'Invalid email format' },
            ]}
          >
            <Input placeholder="user@example.com" />
          </Form.Item>
          {!editingUser && (
            <Form.Item
              name="password"
              label="Password"
              rules={[{ required: true, message: 'Password is required' }]}
            >
              <Input.Password placeholder="Password" />
            </Form.Item>
          )}
          <Form.Item
            name="role"
            label="Role"
            rules={[{ required: true, message: 'Role is required' }]}
          >
            <Select>
              <Select.Option value="admin">Admin</Select.Option>
              <Select.Option value="operator">Operator</Select.Option>
              <Select.Option value="viewer">Viewer</Select.Option>
            </Select>
          </Form.Item>
          <Form.Item name="enabled" label="Enabled" valuePropName="checked">
            <Switch />
          </Form.Item>
        </Form>
      </Modal>

      {/* Reset Password Modal */}
      <Modal
        title="Reset Password"
        open={resetModalOpen}
        onCancel={() => setResetModalOpen(false)}
        onOk={handleResetPassword}
        destroyOnClose
      >
        <Form form={resetForm} layout="vertical">
          <Form.Item
            name="new_password"
            label="New Password"
            rules={[{ required: true, message: 'Password is required' }]}
          >
            <Input.Password placeholder="Enter new password" />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  );
}

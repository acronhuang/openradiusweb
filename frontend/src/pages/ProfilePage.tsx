import React, { useState, useEffect } from 'react';
import {
  Row, Col, Card, Typography, Tag, Avatar, Tabs, Form, Input,
  Select, Switch, Segmented, Button, Space, message, Descriptions,
} from 'antd';
import {
  UserOutlined, SaveOutlined, LockOutlined, MailOutlined,
} from '@ant-design/icons';
import api, { extractErrorMessage } from '../api';

const { Title, Text } = Typography;

const ROLE_COLORS: Record<string, string> = {
  admin: 'red',
  operator: 'blue',
  viewer: 'green',
};

const TIMEZONE_OPTIONS = [
  { value: 'UTC', label: 'UTC' },
  { value: 'Asia/Taipei', label: 'Asia/Taipei (GMT+8)' },
  { value: 'Asia/Tokyo', label: 'Asia/Tokyo (GMT+9)' },
  { value: 'Asia/Shanghai', label: 'Asia/Shanghai (GMT+8)' },
  { value: 'Asia/Singapore', label: 'Asia/Singapore (GMT+8)' },
  { value: 'America/New_York', label: 'America/New_York (EST)' },
  { value: 'America/Chicago', label: 'America/Chicago (CST)' },
  { value: 'America/Denver', label: 'America/Denver (MST)' },
  { value: 'America/Los_Angeles', label: 'America/Los_Angeles (PST)' },
  { value: 'Europe/London', label: 'Europe/London (GMT)' },
  { value: 'Europe/Berlin', label: 'Europe/Berlin (CET)' },
  { value: 'Australia/Sydney', label: 'Australia/Sydney (AEST)' },
];

const LANGUAGE_OPTIONS = [
  { value: 'en', label: 'English' },
  { value: 'zh-TW', label: 'Traditional Chinese' },
  { value: 'ja', label: 'Japanese' },
];

interface UserProfile {
  id: string;
  username: string;
  email: string;
  role: string;
  created_at: string;
  last_login: string | null;
}

interface Preferences {
  timezone: string;
  language: string;
  theme: string;
  notifications_enabled: boolean;
}

export default function ProfilePage() {
  const [profile, setProfile] = useState<UserProfile | null>(null);
  const [loading, setLoading] = useState(false);

  // Email form
  const [emailForm] = Form.useForm();
  const [emailSaving, setEmailSaving] = useState(false);

  // Password form
  const [passwordForm] = Form.useForm();
  const [passwordSaving, setPasswordSaving] = useState(false);

  // Preferences form
  const [prefsForm] = Form.useForm();
  const [prefsSaving, setPrefsSaving] = useState(false);

  useEffect(() => {
    loadProfile();
    loadPreferences();
  }, []);

  const loadProfile = async () => {
    setLoading(true);
    try {
      const res = await api.get('/auth/me');
      setProfile(res.data);
      emailForm.setFieldsValue({ email: res.data.email });
    } catch (err) { message.error(extractErrorMessage(err, 'Failed to load profile')); }
    setLoading(false);
  };

  const loadPreferences = async () => {
    try {
      const res = await api.get('/profile/preferences');
      const data = res.data || {};
      prefsForm.setFieldsValue({
        timezone: data.timezone || 'UTC',
        language: data.language || 'en',
        theme: data.theme || 'Light',
        notifications_enabled: data.notifications_enabled ?? true,
      });
    } catch (err) { message.error(extractErrorMessage(err, 'Failed to load profile')); }
  };

  const handleEmailSave = async () => {
    try {
      const values = await emailForm.validateFields();
      setEmailSaving(true);
      await api.put('/profile/email', { email: values.email });
      message.success('Email updated');
      loadProfile();
    } catch (err) {
      message.error(extractErrorMessage(err, 'Failed to update email'));
    } finally {
      setEmailSaving(false);
    }
  };

  const handlePasswordSave = async () => {
    try {
      const values = await passwordForm.validateFields();
      setPasswordSaving(true);
      await api.put('/profile/password', {
        current_password: values.current_password,
        new_password: values.new_password,
      });
      message.success('Password changed successfully');
      passwordForm.resetFields();
    } catch (err) {
      message.error(extractErrorMessage(err, 'Failed to change password'));
    } finally {
      setPasswordSaving(false);
    }
  };

  const handlePrefsSave = async () => {
    try {
      const values = await prefsForm.validateFields();
      setPrefsSaving(true);
      await api.put('/profile/preferences', values);
      message.success('Preferences saved');
    } catch (err) {
      message.error(extractErrorMessage(err, 'Failed to save preferences'));
    } finally {
      setPrefsSaving(false);
    }
  };

  const accountTab = (
    <Space direction="vertical" size="large" style={{ width: '100%' }}>
      {/* Change Email */}
      <Card size="small" title={<Space><MailOutlined />Change Email</Space>}>
        <Form form={emailForm} layout="inline">
          <Form.Item
            name="email"
            rules={[
              { required: true, message: 'Email is required' },
              { type: 'email', message: 'Invalid email format' },
            ]}
          >
            <Input placeholder="new@example.com" style={{ width: 300 }} />
          </Form.Item>
          <Form.Item>
            <Button
              type="primary"
              icon={<SaveOutlined />}
              onClick={handleEmailSave}
              loading={emailSaving}
            >
              Save
            </Button>
          </Form.Item>
        </Form>
      </Card>

      {/* Change Password */}
      <Card size="small" title={<Space><LockOutlined />Change Password</Space>}>
        <Form form={passwordForm} layout="vertical" style={{ maxWidth: 400 }}>
          <Form.Item
            name="current_password"
            label="Current Password"
            rules={[{ required: true, message: 'Current password is required' }]}
          >
            <Input.Password placeholder="Current password" />
          </Form.Item>
          <Form.Item
            name="new_password"
            label="New Password"
            rules={[{ required: true, message: 'New password is required' }]}
          >
            <Input.Password placeholder="New password" />
          </Form.Item>
          <Form.Item
            name="confirm_password"
            label="Confirm New Password"
            dependencies={['new_password']}
            rules={[
              { required: true, message: 'Please confirm your password' },
              ({ getFieldValue }) => ({
                validator(_, value) {
                  if (!value || getFieldValue('new_password') === value) {
                    return Promise.resolve();
                  }
                  return Promise.reject(new Error('Passwords do not match'));
                },
              }),
            ]}
          >
            <Input.Password placeholder="Confirm new password" />
          </Form.Item>
          <Form.Item>
            <Button
              type="primary"
              icon={<SaveOutlined />}
              onClick={handlePasswordSave}
              loading={passwordSaving}
            >
              Change Password
            </Button>
          </Form.Item>
        </Form>
      </Card>
    </Space>
  );

  const preferencesTab = (
    <Form form={prefsForm} layout="vertical" style={{ maxWidth: 500 }}>
      <Form.Item name="timezone" label="Timezone">
        <Select
          showSearch
          options={TIMEZONE_OPTIONS}
          optionFilterProp="label"
        />
      </Form.Item>
      <Form.Item name="language" label="Language">
        <Select options={LANGUAGE_OPTIONS} />
      </Form.Item>
      <Form.Item name="theme" label="Theme">
        <Segmented options={['Light', 'Dark']} />
      </Form.Item>
      <Form.Item
        name="notifications_enabled"
        label="Notifications"
        valuePropName="checked"
      >
        <Switch checkedChildren="On" unCheckedChildren="Off" />
      </Form.Item>
      <Form.Item>
        <Button
          type="primary"
          icon={<SaveOutlined />}
          onClick={handlePrefsSave}
          loading={prefsSaving}
        >
          Save Preferences
        </Button>
      </Form.Item>
    </Form>
  );

  return (
    <div>
      <Title level={4}>My Profile</Title>
      <Row gutter={24}>
        {/* Left Column - Profile Card */}
        <Col span={8}>
          <Card loading={loading}>
            <div style={{ textAlign: 'center', padding: '16px 0' }}>
              <Avatar size={80} icon={<UserOutlined />} style={{ backgroundColor: '#1677ff' }} />
              <Title level={4} style={{ marginTop: 16, marginBottom: 4 }}>
                {profile?.username || '-'}
              </Title>
              <Tag color={ROLE_COLORS[profile?.role || ''] || 'default'} style={{ fontSize: 14 }}>
                {profile?.role || '-'}
              </Tag>
            </div>
            <Descriptions column={1} size="small" style={{ marginTop: 16 }}>
              <Descriptions.Item label="Email">
                {profile?.email || '-'}
              </Descriptions.Item>
              <Descriptions.Item label="Member Since">
                {profile?.created_at ? new Date(profile.created_at).toLocaleDateString() : '-'}
              </Descriptions.Item>
              <Descriptions.Item label="Last Login">
                {profile?.last_login ? new Date(profile.last_login).toLocaleString() : 'Never'}
              </Descriptions.Item>
            </Descriptions>
          </Card>
        </Col>

        {/* Right Column - Account / Preferences */}
        <Col span={16}>
          <Card>
            <Tabs items={[
              { key: 'account', label: 'Account', children: accountTab },
              { key: 'preferences', label: 'Preferences', children: preferencesTab },
            ]} />
          </Card>
        </Col>
      </Row>
    </div>
  );
}

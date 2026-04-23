import React, { useState, useEffect } from 'react';
import { BrowserRouter, Routes, Route, Navigate, useNavigate, useLocation } from 'react-router-dom';
import { ConfigProvider, Layout, Menu, theme, Button, Avatar, Dropdown, message } from 'antd';
import {
  DashboardOutlined, LaptopOutlined, ApartmentOutlined,
  SafetyOutlined, AuditOutlined, SettingOutlined, LogoutOutlined,
  UserOutlined, SwapOutlined, SafetyCertificateOutlined,
  TeamOutlined, ToolOutlined, FileSearchOutlined, GlobalOutlined,
  KeyOutlined, CloudServerOutlined,
} from '@ant-design/icons';
import type { MenuProps } from 'antd';
import Dashboard from './pages/Dashboard';
import Devices from './pages/Devices';
import Switches from './pages/Switches';
import Policies from './pages/Policies';
import AccessTracker from './pages/AccessTracker';
import CoAPage from './pages/CoAPage';
import LoginPage from './pages/LoginPage';
import ProfilePage from './pages/ProfilePage';
import LdapServers from './pages/radius/LdapServers';
import Realms from './pages/radius/Realms';
import CertificatesPage from './pages/radius/CertificatesPage';
import NasClients from './pages/radius/NasClients';
import FreeRadiusConfig from './pages/radius/FreeRadiusConfig';
import VlanManagement from './pages/radius/VlanManagement';
import MabDevices from './pages/radius/MabDevices';
import GroupVlanMappings from './pages/radius/GroupVlanMappings';
import Dot1xOverview from './pages/Dot1xOverview';
import UserManagement from './pages/settings/UserManagement';
import SystemSettings from './pages/settings/SystemSettings';
import AuditLog from './pages/settings/AuditLog';
import ProtectedRoute from './components/ProtectedRoute';
import api from './api';

const { Header, Sider, Content } = Layout;

/** Decode JWT payload (no verification — just for display). */
function getTokenUsername(): string {
  try {
    const token = localStorage.getItem('orw_token');
    if (!token) return 'User';
    const payload = JSON.parse(atob(token.split('.')[1]));
    return payload.username || 'User';
  } catch {
    return 'User';
  }
}

function AppLayout() {
  const [collapsed, setCollapsed] = useState(false);
  const navigate = useNavigate();
  const location = useLocation();
  const displayName = getTokenUsername();

  const menuItems: MenuProps['items'] = [
    { key: '/', icon: <DashboardOutlined />, label: 'Dashboard' },
    { key: '/devices', icon: <LaptopOutlined />, label: 'Devices' },
    { key: '/switches', icon: <ApartmentOutlined />, label: 'Switches' },
    { key: '/policies', icon: <SafetyOutlined />, label: 'Policies' },
    { key: '/access-tracker', icon: <AuditOutlined />, label: 'Access Tracker' },
    { key: '/coa', icon: <SwapOutlined />, label: 'CoA' },
    { type: 'divider' },
    { key: '/dot1x', icon: <SafetyCertificateOutlined />, label: '802.1X Overview' },
    {
      key: 'radius-config',
      icon: <CloudServerOutlined />,
      label: 'RADIUS Config',
      children: [
        { key: '/radius/ldap', icon: <TeamOutlined />, label: 'LDAP Servers' },
        { key: '/radius/realms', icon: <GlobalOutlined />, label: 'Realms' },
        { key: '/radius/certificates', icon: <SafetyCertificateOutlined />, label: 'Certificates' },
        { key: '/radius/nas-clients', icon: <KeyOutlined />, label: 'NAS Clients' },
        { key: '/radius/vlans', icon: <ApartmentOutlined />, label: 'VLANs' },
        { key: '/radius/mab-devices', icon: <LaptopOutlined />, label: 'MAB Devices' },
        { key: '/radius/group-vlans', icon: <TeamOutlined />, label: 'Dynamic VLAN' },
        { key: '/radius/config', icon: <ToolOutlined />, label: 'FreeRADIUS' },
      ],
    },
    {
      key: 'settings-group',
      icon: <SettingOutlined />,
      label: 'Settings',
      children: [
        { key: '/settings/users', icon: <UserOutlined />, label: 'Users' },
        { key: '/settings/system', icon: <SettingOutlined />, label: 'System' },
        { key: '/settings/audit', icon: <FileSearchOutlined />, label: 'Audit Log' },
      ],
    },
  ];

  const userMenu: MenuProps['items'] = [
    {
      key: 'profile', icon: <UserOutlined />, label: 'Profile',
      onClick: () => navigate('/profile'),
    },
    { type: 'divider' },
    {
      key: 'logout', icon: <LogoutOutlined />, label: 'Logout',
      onClick: () => {
        localStorage.removeItem('orw_token');
        navigate('/login');
      },
    },
  ];

  // Determine selected key for nested menu items
  const selectedKeys = [location.pathname];
  const openKeys = [];
  if (location.pathname.startsWith('/radius/')) openKeys.push('radius-config');
  if (location.pathname.startsWith('/settings/')) openKeys.push('settings-group');

  return (
    <Layout style={{ minHeight: '100vh' }}>
      <Sider collapsible collapsed={collapsed} onCollapse={setCollapsed}
        style={{ background: '#001529' }} width={220}>
        <div style={{
          height: 64, display: 'flex', alignItems: 'center',
          justifyContent: 'center', color: '#fff', fontSize: collapsed ? 16 : 20,
          fontWeight: 'bold', borderBottom: '1px solid rgba(255,255,255,0.1)',
        }}>
          {collapsed ? 'ORW' : 'OpenRadiusWeb'}
        </div>
        <Menu theme="dark" mode="inline" selectedKeys={selectedKeys}
          defaultOpenKeys={openKeys}
          items={menuItems} onClick={({ key }) => navigate(key)} />
      </Sider>
      <Layout>
        <Header style={{
          padding: '0 24px', background: '#fff',
          display: 'flex', justifyContent: 'space-between', alignItems: 'center',
          boxShadow: '0 1px 4px rgba(0,0,0,0.08)',
        }}>
          <span style={{ fontSize: 16, fontWeight: 500 }}>
            Network Access Control
          </span>
          <Dropdown menu={{ items: userMenu }}>
            <Button type="text" icon={<Avatar icon={<UserOutlined />} size="small" />}>
              {' '}{displayName}
            </Button>
          </Dropdown>
        </Header>
        <Content style={{ margin: 16, padding: 24, background: '#fff', borderRadius: 8, minHeight: 360 }}>
          <Routes>
            <Route path="/" element={<Dashboard />} />
            <Route path="/devices" element={<Devices />} />
            <Route path="/switches" element={<Switches />} />
            <Route path="/policies" element={<Policies />} />
            <Route path="/access-tracker" element={<AccessTracker />} />
            <Route path="/coa" element={<CoAPage />} />
            <Route path="/profile" element={<ProfilePage />} />
            <Route path="/radius/ldap" element={<LdapServers />} />
            <Route path="/radius/realms" element={<Realms />} />
            <Route path="/radius/certificates" element={<CertificatesPage />} />
            <Route path="/radius/nas-clients" element={<NasClients />} />
            <Route path="/radius/vlans" element={<VlanManagement />} />
            <Route path="/radius/mab-devices" element={<MabDevices />} />
            <Route path="/radius/group-vlans" element={<GroupVlanMappings />} />
            <Route path="/radius/config" element={<FreeRadiusConfig />} />
            <Route path="/dot1x" element={<Dot1xOverview />} />
            <Route path="/settings/users" element={<UserManagement />} />
            <Route path="/settings/system" element={<SystemSettings />} />
            <Route path="/settings/audit" element={<AuditLog />} />
            <Route path="*" element={
              <div style={{ textAlign: 'center', padding: 80 }}>
                <h2>404 - Page Not Found</h2>
                <p>The page you are looking for does not exist.</p>
                <Button type="primary" onClick={() => navigate('/')}>Back to Dashboard</Button>
              </div>
            } />
          </Routes>
        </Content>
      </Layout>
    </Layout>
  );
}

function App() {
  return (
    <ConfigProvider theme={{
      algorithm: theme.defaultAlgorithm,
      token: { colorPrimary: '#1677ff', borderRadius: 6 },
    }}>
      <BrowserRouter>
        <Routes>
          <Route path="/login" element={<LoginPage />} />
          <Route path="/*" element={<ProtectedRoute><AppLayout /></ProtectedRoute>} />
        </Routes>
      </BrowserRouter>
    </ConfigProvider>
  );
}

export default App;

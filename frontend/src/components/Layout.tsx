import { Outlet, useNavigate } from 'react-router-dom';
import { useAuth } from '../auth/AuthContext';
import { useNotificationToasts } from '../hooks/useNotificationToasts';
import NotificationBell from './NotificationBell';

const ROLE_LABEL: Record<string, string> = {
  WORKER: '근로자',
  OFFICE: '인력사무소',
  COMPANY: '건설사',
};

const ROLE_COLOR: Record<string, string> = {
  WORKER: 'bg-green-600',
  OFFICE: 'bg-purple-600',
  COMPANY: 'bg-orange-600',
};

export default function Layout() {
  const { user, logout } = useAuth();
  const navigate = useNavigate();

  // 로그인 상태에서 새 알림을 toast로 표시
  useNotificationToasts(!!user);

  const handleLogout = () => {
    logout();
    navigate('/login', { replace: true });
  };

  const handleHome = () => navigate(`/${user?.role.toLowerCase()}`);

  if (!user) return null;

  return (
    <div className="min-h-screen bg-gray-50">
      {/* 헤더 */}
      <header className="bg-white border-b border-gray-200 px-3 sm:px-6 py-3 flex items-center justify-between gap-2">
        <div className="flex items-center gap-2 sm:gap-3 min-w-0">
          <button onClick={handleHome} className="text-lg font-bold text-gray-800 hover:text-green-700 transition-colors shrink-0"
            aria-label="CrewMate 메인 화면으로 이동">CrewMate</button>
          <span className={`text-xs text-white px-2 py-0.5 rounded-full ${ROLE_COLOR[user.role]}`}>
            {ROLE_LABEL[user.role]}
          </span>
        </div>
        <div className="flex items-center gap-2 sm:gap-4 min-w-0">
          <NotificationBell />
          <span className="hidden sm:block text-sm text-gray-600 max-w-32 truncate">{user.name}</span>
          <button
            onClick={handleLogout}
            className="text-sm text-gray-500 hover:text-gray-800 transition-colors"
          >
            로그아웃
          </button>
        </div>
      </header>

      {/* 콘텐츠 */}
      <main className="p-3 sm:p-6 min-w-0">
        <Outlet />
      </main>
    </div>
  );
}

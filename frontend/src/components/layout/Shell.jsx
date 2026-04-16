import Header from './Header.jsx'
import TabBar from './TabBar.jsx'

export default function Shell({ children, isAuthenticated }) {
  return (
    <div className="app-shell">
      <Header />
      <TabBar isAuthenticated={isAuthenticated} />
      <main className="shell-content">{children}</main>
    </div>
  )
}

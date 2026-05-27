import { SideNav } from "./components/SideNav";
import { MarketingPage } from "./pages/MarketingPage";

export function App() {
  const path = window.location.pathname;

  return (
    <>
      <SideNav activePath={path} />
      <MarketingPage />
    </>
  );
}

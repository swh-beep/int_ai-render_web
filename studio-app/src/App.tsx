import { SideNav } from "./components/SideNav";
import { AppHomePage } from "./pages/AppHomePage";
import { ImageStudioPage } from "./pages/ImageStudioPage";
import { MarketingPage } from "./pages/MarketingPage";
import { VideoStudioPage } from "./pages/VideoStudioPage";

export function App() {
  const path = window.location.pathname;

  let page = <AppHomePage />;
  if (path.startsWith("/app/marketing")) {
    page = <MarketingPage />;
  } else if (path.startsWith("/app/image-studio")) {
    page = <ImageStudioPage />;
  } else if (path.startsWith("/app/video-studio")) {
    page = <VideoStudioPage />;
  }

  return (
    <>
      <SideNav activePath={path} />
      {page}
    </>
  );
}

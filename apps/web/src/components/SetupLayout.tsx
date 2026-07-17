import { AnimatePresence, motion } from "framer-motion";
import { Outlet, useLocation } from "react-router-dom";
import { Logo } from "./Logo";

/** Shared chrome for every /setup/* route: header, step indicator and an
 *  animated, route-keyed content area using a centered card layout. */
export function SetupLayout() {
  const location = useLocation();
  return (
    <div className="relative flex min-h-dvh flex-col bg-background">
      {/* Subtle dotted backdrop, fading toward the centre. */}
      <div className="pointer-events-none absolute inset-0 bg-grid opacity-40 [mask-image:radial-gradient(ellipse_at_center,black,transparent_75%)]" />

      <header className="relative z-10 flex items-center justify-between px-6 py-5">
        <Logo />
        <a
          href="#"
          className="text-sm text-muted-foreground transition-colors hover:text-foreground"
        >
          Need help?
        </a>
      </header>

      <main className="relative z-10 flex flex-1 flex-col items-center px-4 pb-16 pt-2">
        <div className="w-full max-w-lg">
          <AnimatePresence mode="wait">
            <motion.div
              key={location.pathname}
              initial={{ opacity: 0, y: 12 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -12 }}
              transition={{ duration: 0.28, ease: "easeOut" }}
            >
              <Outlet />
            </motion.div>
          </AnimatePresence>
        </div>
      </main>
    </div>
  );
}

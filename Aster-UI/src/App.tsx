import { AnimatePresence, motion, MotionConfig } from "framer-motion";
import { useProfile } from "./state/useProfile";
import { ChatSessionProvider } from "./state/useChatSession";
import { OnboardingFlow } from "./onboarding/OnboardingFlow";
import { AppShell } from "./shell/AppShell";
import { fade } from "./theme/motion";
import { AVAILABLE_MODELS } from "./mock/models";

function App() {
  const {
    profile,
    setName,
    setTechLevel,
    setModel,
    completeOnboarding,
    resetOnboarding,
  } = useProfile();

  return (
    // reducedMotion="user" makes every framer-motion animation in the app respect
    // the OS-level "prefers-reduced-motion" setting automatically.
    <MotionConfig reducedMotion="user">
      <div className="h-full w-full">
        <AnimatePresence mode="wait">
          {!profile.onboardingComplete ? (
            <motion.div key="onboarding" variants={fade} initial="initial" animate="animate" exit="exit" className="h-full">
              <OnboardingFlow
                initialName={profile.name}
                onComplete={({ name, techLevel, model }) => {
                  setName(name);
                  setTechLevel(techLevel);
                  setModel(model);
                  completeOnboarding();
                }}
              />
            </motion.div>
          ) : (
            <motion.div key="shell" variants={fade} initial="initial" animate="animate" exit="exit" className="h-full">
              <ChatSessionProvider>
                <AppShell
                  profile={profile}
                  onUpdateName={setName}
                  onUpdateTechLevel={setTechLevel}
                  onUpdateModelId={(modelId) => {
                    const model = AVAILABLE_MODELS.find((m) => m.id === modelId);
                    if (model) setModel(model);
                  }}
                  onResetOnboarding={resetOnboarding}
                />
              </ChatSessionProvider>
            </motion.div>
          )}
        </AnimatePresence>
      </div>
    </MotionConfig>
  );
}

export default App;

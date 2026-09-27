import { useState } from "react";
import { motion } from "framer-motion";
import { fadeSlideUp } from "../theme/motion";
import { Input } from "../components/ui/Input";
import { Button } from "../components/ui/Button";

interface NameStepProps {
  initialName: string;
  onContinue: (name: string) => void;
}

export function NameStep({ initialName, onContinue }: NameStepProps) {
  const [name, setName] = useState(initialName);

  return (
    <motion.div
      variants={fadeSlideUp}
      initial="initial"
      animate="animate"
      exit="exit"
      className="flex w-full max-w-sm flex-col items-center text-center"
    >
      <h1 className="font-headline-lg text-headline-lg text-white/90">Tell us your name</h1>
      <p className="mt-2 text-body-md text-on-surface-variant/70">
        Aster will use this to address you.
      </p>

      <form
        className="mt-8 flex w-full flex-col gap-4"
        onSubmit={(e) => {
          e.preventDefault();
          if (name.trim()) onContinue(name.trim());
        }}
      >
        <Input
          autoFocus
          placeholder="Your name"
          value={name}
          onChange={(e) => setName(e.target.value)}
        />
        <Button type="submit" disabled={!name.trim()}>
          Continue
        </Button>
      </form>
    </motion.div>
  );
}

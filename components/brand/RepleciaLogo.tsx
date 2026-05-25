import Image from "next/image";

type RepleciaLogoProps = {
  markOnly?: boolean;
  className?: string;
};

export default function RepleciaLogo({ markOnly = false, className = "" }: RepleciaLogoProps) {
  if (markOnly) {
    return (
      <Image
        className={`replecia-logo-image replecia-logo-image--mark ${className}`}
        src="/replecia-mark.svg"
        alt="ReplecIA"
        width={48}
        height={48}
        priority
      />
    );
  }

  return (
    <Image
      className={`replecia-logo-image ${className}`}
      src="/logo.png"
      alt="ReplecIA"
      width={826}
      height={584}
      priority
    />
  );
}

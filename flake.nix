{
  description = "mailkit — terminal mail with a sealed local mirror per account";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";

  outputs = { self, nixpkgs }:
    let
      systems = [ "x86_64-linux" "aarch64-linux" ];
      forAll = f: nixpkgs.lib.genAttrs systems (system: f nixpkgs.legacyPackages.${system});

      # What mailkit calls. fusermount is deliberately NOT here: it has to be
      # the system's setuid one (/run/wrappers/bin on NixOS), found on PATH.
      runtimeDeps = pkgs: [
        pkgs.isync      # mbsync
        pkgs.notmuch
        pkgs.gocryptfs
        pkgs.libsecret  # secret-tool
        pkgs.systemd    # busctl (mailkit keyrings)
        pkgs.aerc       # optional reader
      ];
    in
    {
      packages = forAll (pkgs: rec {
        mailkit = pkgs.python3Packages.buildPythonApplication {
          pname = "mailkit";
          version = "0.1.0";
          pyproject = true;
          src = ./.;
          build-system = [ pkgs.python3Packages.setuptools ];
          makeWrapperArgs = [
            "--prefix" "PATH" ":" (pkgs.lib.makeBinPath (runtimeDeps pkgs))
            # Called back by mbsync/gocryptfs/aerc: the wrapper, so they get this PATH too.
            "--set" "MAILKIT_BIN" "${placeholder "out"}/bin/mailkit"
          ];
          nativeCheckInputs = [ pkgs.notmuch ];
          checkPhase = ''
            runHook preCheck
            PYTHONPATH=src python -m unittest discover -s tests -t .
            runHook postCheck
          '';
          postInstall = ''
            install -Dm644 completions/mailkit.fish $out/share/fish/vendor_completions.d/mailkit.fish
          '';
          meta.mainProgram = "mailkit";
        };
        default = mailkit;
      });

      overlays.default = final: prev: { mailkit = self.packages.${prev.stdenv.hostPlatform.system}.mailkit; };

      devShells = forAll (pkgs: {
        default = pkgs.mkShell {
          packages = [ pkgs.python3 pkgs.ruff ] ++ runtimeDeps pkgs;
        };
      });
    };
}

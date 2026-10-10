# Shared-library search path for Playwright's Ubuntu 24.04 browser builds.
# Match the stable-24_05 channel declared in .replit.
let
  pkgs = import <nixpkgs> {};
in {
libraryPath = pkgs.lib.makeLibraryPath (with pkgs; [
  gtk3 glib dbus alsa-lib
  xorg.libXcomposite xorg.libXdamage xorg.libXrandr xorg.libXcursor
  xorg.libXi xorg.libXext xorg.libXfixes
  libxkbcommon pango cairo gdk-pixbuf atk
  icu74 libdrm mesa libGL libepoxy libevent libgudev libsecret
  enchant2 flite libtasn1 hyphen libmanette woff2
  libxml2 libxslt sqlite harfbuzzFull libopus libjpeg8 libavif
  gcc14.cc.lib
  gst_all_1.gstreamer gst_all_1.gst-plugins-base
  gst_all_1.gst-plugins-good gst_all_1.gst-plugins-bad
]);
mesaDrivers = toString pkgs.mesa.drivers;
}

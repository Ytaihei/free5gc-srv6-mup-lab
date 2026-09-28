# Third-party software and redistribution notes

English | [日本語](THIRD_PARTY_NOTICES.ja.md)

This repository contains lab automation, configuration, and original source
code. It does not vendor the complete source trees, container images, VM
images, or compiled binaries of the runtime projects listed below. The
provisioning process downloads those projects from their upstream locations.

| Component | Pinned baseline | How it is used | Upstream license |
|---|---|---|---|
| Vinbero | v0.1.1 / 0d7ccf3c798fffa000ccee82e1fd8c9d86525099 | Cloned and patched during PE provisioning | [Apache License 2.0](https://github.com/takehaya/Vinbero/blob/0d7ccf3c798fffa000ccee82e1fd8c9d86525099/LICENSE) |
| free5gc-compose | v4.2.3 | Cloned during core provisioning | [Apache License 2.0](https://github.com/free5gc/free5gc-compose/blob/e4e2acebad6d6a8c49cfb03a97d9a09dd40c55c7/LICENSE.txt) |
| gtp5g | v0.9.5 | Cloned, built, and installed as a kernel module | [GNU GPL version 2](https://github.com/free5gc/gtp5g/blob/973d001b25832c5a8e8d34f6381eb0c705fb523d/LICENSE) |
| UERANSIM | v3.3.0 | Cloned and built on the RAN VM | [GNU Affero GPL version 3](https://github.com/aligungr/UERANSIM/blob/6bf5a1a96aaef6ae8778b9d8b477ac6e2bbf8156/LICENSE) |
| MongoDB Community Server | Reference: upstream 4.4; fresh compact: pinned 8.0.32 | Pulled as a container image; existing data requires explicit migration | [Server Side Public License](https://www.mongodb.com/legal/licensing/server-side-public-license) |

The Vinbero compatibility patch under third_party/vinbero is a modification
against the pinned Apache-2.0 source. Its provenance and purpose are documented
in third_party/vinbero/README.md. The complete Apache-2.0 license text is in
LICENSE.

The fixed upstream source licenses above were checked on 2026-09-07.
Vinbero has no separate NOTICE file in that pinned source tree. Preserve this
attribution, the patch modification record and the Apache license in any
source candidate. UERANSIM also offers a commercial license according to its
[pinned README](https://github.com/aligungr/UERANSIM/blob/6bf5a1a96aaef6ae8778b9d8b477ac6e2bbf8156/README.md);
this lab records the AGPL option and does not claim commercial-license rights.

Go dependencies are resolved from the versions and checksums in go.mod and
go.sum. Their source and license files are not copied into this repository.
Before publishing compiled Go binaries, generate a complete dependency license
inventory and include the applicable notices with the release.

## Release policy

The compact image review uses a separate license classification and private
material collection workflow. See [image distribution review](docs/image-distribution.md)
for dependency updates, public default/test-key provenance, required sources/notices and
remaining evidence gaps. Recognizing GPL/LGPL does not by itself approve an image.

Until the distribution milestone is complete, publish this repository as
source only. Do not attach qcow2 images, downloaded Ubuntu images, Docker image
archives, patched Vinbero binaries, UERANSIM binaries, or gtp5g kernel modules
to a release.

Source-only packaging does not approve redistribution of downloaded runtimes
or operation of a third-party software service. In particular, AGPL and SSPL
have network/service provisions; assess the actual deployment before exposing
those programs to third parties. The current review is limited to this
repository's source snapshot, not an entire appliance or a legal compliance
certification. See [source distribution](docs/source-distribution.md).

Any future binary or image release must include:

- an SBOM for the exact artifact;
- all licenses and notices required by included software;
- corresponding source or a compliant source offer where required; and
- the version and digest lock manifest used to build the artifact.

This inventory is an engineering record, not legal advice. Confirm applicable
obligations before distributing binary artifacts or offering a third-party
service.

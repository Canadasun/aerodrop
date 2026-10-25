terraform {
  required_version = ">= 1.5.0"
}

variable "project_dir" {
  description = "Absolute path to the AeroDrop repository root. Defaults to this module's parent directory."
  type        = string
  default     = ""
}

variable "compose_profiles" {
  description = "Optional Docker Compose profiles to enable."
  type        = list(string)
  default     = []
}

locals {
  root = var.project_dir != "" ? abspath(var.project_dir) : abspath("${path.module}/../..")
  backend_source_hash = sha256(join("", [
    for path in sort(fileset("${local.root}/backend", "**")) :
    filesha256("${local.root}/backend/${path}") if !strcontains(path, "__pycache__")
  ]))
  ros_source_hash = sha256(join("", [
    for path in sort(fileset("${local.root}/ros2_ws/src/aerodrop_core", "**")) :
    filesha256("${local.root}/ros2_ws/src/aerodrop_core/${path}")
  ]))
}

resource "terraform_data" "aerodrop_stack" {
  # Terraform owns the local prototype lifecycle while Compose remains the
  # portable container specification used by both developers and CI.
  triggers_replace = {
    compose = filesha256("${local.root}/docker-compose.yml")
    backend = local.backend_source_hash
    ros     = local.ros_source_hash
    ros_img = filesha256("${local.root}/ros2_ws/Dockerfile")
    world   = filesha256("${local.root}/simulation/worlds/calgary_suburb.sdf")
    map     = filesha256("${local.root}/frontend/index.html")
  }

  provisioner "local-exec" {
    working_dir = local.root
    command     = "docker compose ${join(" ", [for profile in var.compose_profiles : "--profile ${profile}"])} up --build -d"
  }

  provisioner "local-exec" {
    when        = destroy
    working_dir = self.input.root
    command     = "docker compose down"
  }

  input = {
    root = local.root
  }
}

output "project_dir" {
  value       = local.root
  description = "Project directory managed by this Terraform configuration."
}

CREATE TABLE `comparison_votes` (
	`capture_id` text NOT NULL,
	`observer_hash` text NOT NULL,
	`choice` text NOT NULL,
	`created_at` text DEFAULT CURRENT_TIMESTAMP NOT NULL,
	`updated_at` text DEFAULT CURRENT_TIMESTAMP NOT NULL,
	PRIMARY KEY(`capture_id`, `observer_hash`)
);
--> statement-breakpoint
CREATE INDEX `comparison_votes_capture_choice_idx` ON `comparison_votes` (`capture_id`,`choice`);